import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from mutagen.mp3 import MP3
from PIL import Image


QUALITIES = {"1080P": (1920, 1080), "2K": (2560, 1440), "4K": (3840, 2160)}
FPS_OPTIONS = (24, 25, 30, 50, 60)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")


@dataclass
class BatchFolder:
    name: str
    path: Path
    images: list[Path]
    audios: list[Path]


@dataclass
class TimelineVideoJob:
    timeline_path: Path
    audio_path: Path
    output_path: Path
    audio_duration: float
    entries: list[dict]


def natural_key(value):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(value))]


def sorted_files(folder, extensions):
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(
        (path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in extensions),
        key=lambda path: natural_key(path.name),
    )


def discover_projects():
    projects = {}
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    base = Path(local_appdata) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    if not base.is_dir():
        return projects
    for folder in base.iterdir():
        if not folder.is_dir() or not (folder / "draft_content.json").is_file():
            continue
        project_name = folder.name
        try:
            with open(folder / "draft_meta_info.json", "r", encoding="utf-8") as handle:
                project_name = json.load(handle).get("draft_name") or folder.name
        except (OSError, json.JSONDecodeError):
            pass
        display = f"{project_name} [{folder.name}]"
        unique, suffix = display, 2
        while unique in projects:
            unique = f"{display} ({suffix})"
            suffix += 1
        projects[unique] = folder
    return dict(sorted(projects.items(), reverse=True))


def parse_zoom_settings(minimum, maximum, difference):
    try:
        minimum, maximum, difference = float(minimum), float(maximum), float(difference)
    except ValueError as exc:
        raise ValueError("Zoom Min, Zoom Max và Min Diff phải là số.") from exc
    if minimum <= 0 or maximum <= 0:
        raise ValueError("Zoom Min và Zoom Max phải lớn hơn 0.")
    if minimum > maximum:
        raise ValueError("Zoom Min không được lớn hơn Zoom Max.")
    if difference < 0 or difference > maximum - minimum:
        raise ValueError("Min Diff phải từ 0 đến chênh lệch Zoom Max và Zoom Min.")
    return minimum, maximum, difference


def validate_quality(quality):
    if quality not in QUALITIES:
        raise ValueError("Độ phân giải phải là 1080P, 2K hoặc 4K.")
    return quality


def validate_fps(fps):
    try:
        fps = int(fps)
    except (TypeError, ValueError) as exc:
        raise ValueError("FPS không hợp lệ.") from exc
    if fps not in FPS_OPTIONS:
        raise ValueError(f"FPS phải là một trong các mức: {', '.join(map(str, FPS_OPTIONS))}.")
    return fps


def build_pairs(images, audios):
    if not images or not audios:
        raise ValueError("Folder MP3 hoặc folder ảnh đang trống.")
    if len(images) != len(audios):
        raise ValueError(f"Số lượng không khớp: {len(images)} ảnh / {len(audios)} MP3.")
    pairs, current = [], 0
    for image_path, audio_path in zip(images, audios):
        duration = int(MP3(str(audio_path)).info.length * 1_000_000)
        pairs.append({
            "mp3_path": str(audio_path), "img_path": str(image_path),
            "duration": duration, "start_time": current,
        })
        current += duration
    return pairs


def build_basic_timeline(json_path, pairs):
    with open(json_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    materials = data.setdefault("materials", {})
    for key in ("videos", "audios", "speeds", "volumes", "canvases"):
        materials.setdefault(key, [])
    tracks = data.setdefault("tracks", [])
    video_track = next((track for track in tracks if track.get("type") == "video"), None)
    if not video_track:
        video_track = {"attribute": 0, "flag": 0, "id": str(uuid.uuid4()), "type": "video", "segments": []}
        tracks.insert(0, video_track)
    audio_track = next((track for track in tracks if track.get("type") == "audio"), None)
    if not audio_track:
        audio_track = {"id": str(uuid.uuid4()), "type": "audio", "segments": []}
        tracks.append(audio_track)
    video_track.setdefault("segments", [])
    audio_track.setdefault("segments", [])

    for item in pairs:
        audio_id, video_id = str(uuid.uuid4()), str(uuid.uuid4())
        speed_id, canvas_id = str(uuid.uuid4()), str(uuid.uuid4())
        materials["speeds"].append({
            "curveUpdateEvent": False, "id": speed_id, "mode": 0, "speed": 1.0, "type": "speed",
        })
        materials["canvases"].append({"id": canvas_id, "type": "canvas"})
        materials["audios"].append({
            "id": audio_id, "type": "extract_music", "path": item["mp3_path"],
            "duration": item["duration"], "name": Path(item["mp3_path"]).name,
        })
        materials["videos"].append({
            "id": video_id, "path": item["img_path"], "type": "photo", "duration": item["duration"],
        })
        timerange = {"start": item["start_time"], "duration": item["duration"]}
        audio_track["segments"].append({
            "id": str(uuid.uuid4()), "material_id": audio_id,
            "target_timerange": timerange.copy(),
            "source_timerange": {"start": 0, "duration": item["duration"]},
        })
        video_track["segments"].append({
            "id": str(uuid.uuid4()), "material_id": video_id,
            "target_timerange": timerange.copy(),
            "source_timerange": {"start": 0, "duration": item["duration"]},
            "extra_material_refs": [speed_id, canvas_id],
        })
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def build_timeline_video_project(json_path, job):
    """Populate a cleared CapCut draft from one timeline-video job."""
    json_path = Path(json_path)
    with open(json_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    materials = data.setdefault("materials", {})
    for key in ("videos", "audios", "speeds", "volumes", "canvases"):
        materials.setdefault(key, [])
    tracks = data.setdefault("tracks", [])
    video_track = next((track for track in tracks if track.get("type") == "video"), None)
    if not video_track:
        video_track = {
            "attribute": 0, "flag": 0, "id": str(uuid.uuid4()),
            "type": "video", "segments": [],
        }
        tracks.insert(0, video_track)
    audio_track = next((track for track in tracks if track.get("type") == "audio"), None)
    if not audio_track:
        audio_track = {"id": str(uuid.uuid4()), "type": "audio", "segments": []}
        tracks.append(audio_track)
    video_track["segments"] = []
    audio_track["segments"] = []

    audio_duration = max(1, round(job.audio_duration * 1_000_000))
    audio_id = str(uuid.uuid4())
    materials["audios"].append({
        "id": audio_id, "type": "extract_music", "path": str(job.audio_path),
        "duration": audio_duration, "name": job.audio_path.name,
    })
    audio_track["segments"].append({
        "id": str(uuid.uuid4()), "material_id": audio_id,
        "target_timerange": {"start": 0, "duration": audio_duration},
        "source_timerange": {"start": 0, "duration": audio_duration},
    })

    for index, entry in enumerate(job.entries):
        start = 0 if index == 0 else round(entry["start"] * 1_000_000)
        end = (
            round(job.entries[index + 1]["start"] * 1_000_000)
            if index + 1 < len(job.entries) else audio_duration
        )
        duration = max(1, end - start)
        video_id, speed_id, canvas_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        materials["speeds"].append({
            "curveUpdateEvent": False, "id": speed_id, "mode": 0,
            "speed": 1.0, "type": "speed",
        })
        materials["canvases"].append({"id": canvas_id, "type": "canvas"})
        materials["videos"].append({
            "id": video_id, "path": str(entry["image"]),
            "type": "photo", "duration": duration,
        })
        timerange = {"start": start, "duration": duration}
        video_track["segments"].append({
            "id": str(uuid.uuid4()), "material_id": video_id,
            "target_timerange": timerange,
            "source_timerange": {"start": 0, "duration": duration},
            "extra_material_refs": [speed_id, canvas_id],
        })

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return len(video_track["segments"])


def apply_perfect_motion(json_path, minimum, maximum, difference):
    with open(json_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    video_tracks = [track for track in data.get("tracks", []) if track.get("type") == "video"]
    track = max(video_tracks, key=lambda item: len(item.get("segments", [])), default=None)
    if not track:
        return 0
    video_materials = data.get("materials", {}).get("videos", [])
    processed = 0
    for segment in track.get("segments", []):
        material = next((item for item in video_materials if item.get("id") == segment.get("material_id")), None)
        if not material or material.get("type") != "photo":
            continue
        try:
            with Image.open(material.get("path")) as image:
                ratio = image.width / image.height
        except (OSError, TypeError):
            continue
        direction = "HORIZONTAL" if ratio > 16 / 9 else "VERTICAL" if ratio < 16 / 9 else "ANY"
        start_percent = random.uniform(minimum, maximum)
        ranges = []
        if start_percent - difference >= minimum:
            ranges.append((minimum, start_percent - difference))
        if start_percent + difference <= maximum:
            ranges.append((start_percent + difference, maximum))
        if ranges:
            end_percent = random.uniform(*random.choice(ranges))
        else:
            end_percent = maximum if abs(maximum - start_percent) > abs(start_percent - minimum) else minimum
        start_scale, end_scale = start_percent / 100, end_percent / 100

        def offsets(scale):
            limit = max(0, (scale - 1) / 2)
            x = random.uniform(-limit, limit) if direction in ("HORIZONTAL", "ANY") else 0.0
            y = random.uniform(-limit, limit) if direction in ("VERTICAL", "ANY") else 0.0
            return x, y

        start_x, start_y = offsets(start_scale)
        end_x, end_y = offsets(end_scale)
        duration = segment["target_timerange"]["duration"]

        def keyframes(kind, first, last):
            return {
                "id": str(uuid.uuid4()), "property_type": kind,
                "keyframe_list": [
                    {"curveType": "Line", "id": str(uuid.uuid4()), "time_offset": 0, "values": [first]},
                    {"curveType": "Line", "id": str(uuid.uuid4()), "time_offset": duration, "values": [last]},
                ],
            }

        segment["common_keyframes"] = [
            keyframes("KFTypeScaleX", start_scale, end_scale),
            keyframes("KFTypePositionX", start_x, end_x),
            keyframes("KFTypePositionY", start_y, end_y),
        ]
        processed += 1
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=4)
    return processed


def create_timeline(project_path, image_folder, audio_folder, zoom):
    project_path = Path(project_path)
    json_path = project_path / "draft_content.json"
    images = sorted_files(image_folder, IMAGE_EXTENSIONS)
    audios = sorted_files(audio_folder, (".mp3",))
    pairs = build_pairs(images, audios)
    backup = Path(str(json_path) + ".backup")
    shutil.copy2(json_path, backup)
    try:
        build_basic_timeline(json_path, pairs)
        motion_count = apply_perfect_motion(json_path, *zoom)
    except Exception:
        shutil.copy2(backup, json_path)
        raise
    return len(pairs), motion_count


def clear_project(project_path, unique_backup=False):
    project_path = Path(project_path)
    json_path, meta_path = project_path / "draft_content.json", project_path / "draft_meta_info.json"
    backup_dir = project_path.parent / "_tool_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if unique_backup:
        stamp += f"-{uuid.uuid4().hex[:6]}"
    shutil.copy2(json_path, backup_dir / f"{project_path.name}-{stamp}-draft_content.json")
    if meta_path.is_file():
        shutil.copy2(meta_path, backup_dir / f"{project_path.name}-{stamp}-draft_meta_info.json")
    with open(json_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    for track in data.get("tracks", []):
        if isinstance(track, dict):
            track["segments"] = []
    for group in data.get("materials", {}).values():
        if isinstance(group, list):
            group.clear()
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    if meta_path.is_file():
        with open(meta_path, "r", encoding="utf-8") as handle:
            meta = json.load(handle)
        for item in meta.get("draft_materials", []):
            if isinstance(item, dict) and isinstance(item.get("value"), list):
                item["value"] = []
        meta.update({
            "tm_duration": 0, "cloud_draft_sync": False, "cloud_draft_cover": False,
            "draft_cloud_last_action_download": False, "draft_id": str(uuid.uuid4()).upper(),
            "tm_draft_cloud_completed": "", "tm_draft_cloud_entry_id": -1,
            "tm_draft_cloud_modified": 0, "tm_draft_cloud_parent_entry_id": -1,
            "tm_draft_cloud_space_id": -1, "tm_draft_cloud_user_id": -1,
        })
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump(meta, handle, ensure_ascii=False, indent=2)


def scan_batch_folders(root_folder):
    jobs, skipped = [], []
    for child in sorted((path for path in Path(root_folder).iterdir() if path.is_dir()), key=lambda path: natural_key(path.name)):
        try:
            if any(path.is_file() and path.suffix.lower() == ".mp4" for path in child.iterdir()):
                skipped.append((child.name, "đã có file MP4"))
                continue
            images = sorted_files(child / "Ảnh", IMAGE_EXTENSIONS)
            audios = sorted_files(child / "full" / "audio", (".mp3",))
            if not images or not audios:
                skipped.append((child.name, f"thiếu dữ liệu (ảnh: {len(images)}, MP3: {len(audios)})"))
            elif len(images) != len(audios):
                skipped.append((child.name, f"số lượng không khớp (ảnh: {len(images)}, MP3: {len(audios)})"))
            else:
                jobs.append(BatchFolder(child.name, child, images, audios))
        except OSError as exc:
            skipped.append((child.name, f"không thể đọc dữ liệu: {exc}"))
    return jobs, skipped


def generated_motion(json_path):
    with open(json_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    video_track = next((track for track in data.get("tracks", []) if track.get("type") == "video"), {})
    motions = []
    for segment in sorted(video_track.get("segments", []), key=lambda item: item.get("target_timerange", {}).get("start", 0)):
        scale = next((item for item in segment.get("common_keyframes", []) if item.get("property_type") == "KFTypeScaleX"), None)
        values = scale.get("keyframe_list", []) if scale else []
        motions.append((float(values[0]["values"][0]), float(values[-1]["values"][0])) if len(values) >= 2 else (1.0, 1.0))
    return motions


@lru_cache(maxsize=4)
def _render_profile(ffmpeg):
    """Probe actual hardware support, not just the encoders compiled into FFmpeg."""
    profiles = (
        ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"], "NVIDIA NVENC"),
        ("h264_qsv", ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "20"], "Intel Quick Sync"),
        ("h264_amf", ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "19", "-qp_p", "20"], "AMD AMF"),
    )
    for encoder, arguments, label in profiles:
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
            "color=c=black:s=64x64:d=0.1", "-frames:v", "1", "-an", "-c:v", encoder,
            "-f", "null", "NUL",
        ]
        try:
            result = subprocess.run(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, creationflags=0x08000000,
            )
            if result.returncode == 0:
                return arguments, label, 2
        except (OSError, subprocess.TimeoutExpired):
            continue
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19"], "CPU x264 (Veryfast)", 1


def _motion_filter(width, height, start_zoom, end_zoom, frames, fps, centered=False):
    """Build a CFR, sub-pixel zoom filter for one still-image span.

    zoompan rounds its crop origin to whole source pixels.  A centered crop moves
    on both axes, so those rounding steps are visible as small shakes even when
    frame timestamps are perfectly regular.  perspective samples at 1/256-pixel
    precision and cubic interpolation, which keeps both the scale and center
    continuous without rendering a huge intermediate image.
    """
    start_zoom = max(1.0, float(start_zoom))
    end_zoom = max(1.0, float(end_zoom))
    progress = "0" if frames <= 1 else f"(on-1)/{frames - 1}"
    zoom = f"({start_zoom:.8f}+({end_zoom:.8f}-{start_zoom:.8f})*{progress})"

    if centered:
        inset_x = f"(W-W/{zoom})/2"
        inset_y = f"(H-H/{zoom})/2"
        corners = (
            f"x0='{inset_x}':y0='{inset_y}':"
            f"x1='W-({inset_x})':y1='{inset_y}':"
            f"x2='{inset_x}':y2='H-({inset_y})':"
            f"x3='W-({inset_x})':y3='H-({inset_y})'"
        )
    else:
        corners = (
            "x0='0':y0='0':"
            f"x1='W/{zoom}':y1='0':"
            f"x2='0':y2='H/{zoom}':"
            f"x3='W/{zoom}':y3='H/{zoom}'"
        )

    return (
        f"scale={width}:{height}:flags=lanczos:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={fps},"
        f"perspective={corners}:sense=source:eval=frame:interpolation=cubic,format=yuv420p"
    )


def render_pairs(name, output_folder, pairs, motions, quality, fps=30, progress=None, cancelled=None,
                 smooth_zoom=False):
    if len(pairs) != len(motions):
        raise RuntimeError("Không thể đọc đủ keyframe zoom từ dự án CapCut.")
    width, height = QUALITIES[validate_quality(quality)]
    fps = validate_fps(fps)
    ffmpeg = shutil.which("ffmpeg") or r"E:\SETUP\ffmpeg-2026-03-18-git-106616f13d-full_build\bin\ffmpeg.exe"
    if not Path(ffmpeg).is_file():
        raise RuntimeError("Không tìm thấy FFmpeg. Hãy thêm ffmpeg vào PATH.")
    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "capcut_video"
    output_path = output_folder / f"{safe_name}_{quality}.mp4"
    pending = output_folder / f".capcut_output_{uuid.uuid4().hex}.tmp"
    temp_dir = Path(tempfile.mkdtemp(prefix=".capcut_render_", dir=output_folder))
    log_path = output_folder / "capcut_export.log"
    encoder_arguments, encoder_label, parallel_jobs = _render_profile(str(ffmpeg))
    cpu_arguments = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19"]
    actual_encoders = set()
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"Encoder: {encoder_label}\nFPS: {fps}\nParallel segments: {parallel_jobs}\n")
            segments = [None] * len(pairs)

            def render_segment(index, pair, motion):
                if cancelled and cancelled():
                    raise InterruptedError("Đã dừng theo yêu cầu.")
                segment = temp_dir / f"segment_{index:04d}.mp4"
                frames = max(1, round(pair["duration"] / 1_000_000 * fps))
                start, end = motion
                video_filter = _motion_filter(
                    width, height, start, end, frames, fps, smooth_zoom
                )
                attempts = [(encoder_arguments, encoder_label)]
                if encoder_label != "CPU x264 (Veryfast)":
                    attempts.append((cpu_arguments, "CPU x264 fallback"))
                errors = []
                for current_arguments, current_label in attempts:
                    command = [
                        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-loop", "1",
                        "-framerate", str(fps), "-i", pair["img_path"],
                        "-i", pair["mp3_path"], "-vf", video_filter, "-map", "0:v", "-map", "1:a",
                        "-t", f"{pair['duration'] / 1_000_000:.6f}", *current_arguments,
                        "-c:a", "aac", "-b:a", "192k", "-shortest", str(segment),
                    ]
                    result = subprocess.run(
                        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        creationflags=0x08000000,
                    )
                    if result.returncode == 0:
                        return index, segment, "", current_label
                    errors.append(result.stdout.decode("utf-8", errors="replace"))
                return index, segment, "\n".join(errors), encoder_label

            completed_count = 0
            with ThreadPoolExecutor(max_workers=parallel_jobs, thread_name_prefix="capcut-render") as executor:
                futures = [
                    executor.submit(render_segment, index, pair, motion)
                    for index, (pair, motion) in enumerate(zip(pairs, motions), 1)
                ]
                for future in as_completed(futures):
                    index, segment, error_details, used_encoder = future.result()
                    if error_details:
                        log.write(f"\nSEGMENT {index} FAILED\n{error_details}\n")
                        raise RuntimeError(f"FFmpeg lỗi khi render đoạn {index}/{len(pairs)}. Xem {log_path}")
                    segments[index - 1] = segment
                    actual_encoders.add(used_encoder)
                    completed_count += 1
                    if progress:
                        progress(completed_count, len(pairs))
                    if cancelled and cancelled():
                        for pending_future in futures:
                            pending_future.cancel()
                        raise InterruptedError("Đã dừng theo yêu cầu.")
            concat_file = temp_dir / "concat.txt"
            with open(concat_file, "w", encoding="utf-8") as handle:
                for segment in segments:
                    handle.write("file '" + str(segment).replace("'", "'\\''") + "'\n")
            result = subprocess.run(
                [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", "-f", "mp4", str(pending)],
                stdout=log, stderr=subprocess.STDOUT, creationflags=0x08000000,
            )
            if result.returncode:
                raise RuntimeError(f"FFmpeg không thể ghép video. Xem {log_path}")
        os.replace(pending, output_path)
        final_encoder = " + ".join(sorted(actual_encoders)) or encoder_label
        return output_path, final_encoder
    finally:
        if pending.exists():
            pending.unlink()
        shutil.rmtree(temp_dir, ignore_errors=True)


def render_project(project_path, project_name, output_folder, quality, fps=30, progress=None, cancelled=None,
                   smooth_zoom=False):
    project_path = Path(project_path)
    with open(project_path / "draft_content.json", "r", encoding="utf-8") as handle:
        data = json.load(handle)
    materials = data.get("materials", {})
    videos = {item.get("id"): item for item in materials.get("videos", [])}
    audios = {item.get("id"): item for item in materials.get("audios", [])}
    video_track = next((track for track in data.get("tracks", []) if track.get("type") == "video"), {})
    audio_track = next((track for track in data.get("tracks", []) if track.get("type") == "audio"), {})
    audio_by_start = {item.get("target_timerange", {}).get("start"): item for item in audio_track.get("segments", [])}
    pairs, motions = [], []
    for segment in sorted(video_track.get("segments", []), key=lambda item: item.get("target_timerange", {}).get("start", 0)):
        timerange = segment.get("target_timerange", {})
        audio_segment = audio_by_start.get(timerange.get("start"))
        image = videos.get(segment.get("material_id"), {}).get("path")
        audio = audios.get(audio_segment.get("material_id"), {}).get("path") if audio_segment else None
        if image and audio and Path(image).is_file() and Path(audio).is_file():
            pairs.append({"img_path": image, "mp3_path": audio, "duration": timerange.get("duration", 0)})
            scale = next((item for item in segment.get("common_keyframes", []) if item.get("property_type") == "KFTypeScaleX"), None)
            values = scale.get("keyframe_list", []) if scale else []
            motions.append((float(values[0]["values"][0]), float(values[-1]["values"][0])) if len(values) >= 2 else (1.0, 1.15))
    if not pairs:
        raise RuntimeError("Dự án không có timeline ảnh/MP3 hợp lệ để xuất.")
    return render_pairs(
        project_name.split(" [", 1)[0], output_folder, pairs, motions, quality,
        fps, progress, cancelled, smooth_zoom,
    )


def _name_prefix(path):
    return Path(path).stem.split("_", 1)[0].strip().casefold()


def _read_timeline(path):
    path = Path(path)
    if path.suffix.lower() != ".json":
        raise ValueError("Chỉ hỗ trợ file timeline .json.")
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Nội dung không phải JSON hợp lệ (dòng {exc.lineno}, cột {exc.colno}).") from exc
    if not isinstance(data, list) or not data:
        raise ValueError("Timeline phải là một mảng JSON không rỗng.")
    entries = []
    previous_end = -1.0
    for position, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Phần tử #{position} phải là object JSON.")
        missing = [key for key in ("start", "end", "duration", "scene") if key not in item]
        if missing:
            raise ValueError(f"Phần tử #{position} thiếu key: {', '.join(missing)}.")
        try:
            start, end, duration = float(item["start"]), float(item["end"]), float(item["duration"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Phần tử #{position}: start/end/duration phải là số.") from exc
        scene = item["scene"]
        if isinstance(scene, bool):
            raise ValueError(f"Phần tử #{position}: scene phải là số nguyên.")
        try:
            numeric_scene = int(scene)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Phần tử #{position}: scene phải là số nguyên.") from exc
        if str(scene).strip() not in (str(numeric_scene), f"{numeric_scene}.0"):
            raise ValueError(f"Phần tử #{position}: scene phải là số nguyên.")
        if start < 0 or end <= start or duration <= 0:
            raise ValueError(f"Phần tử #{position}: thời gian phải thỏa start ≥ 0, end > start, duration > 0.")
        if abs((end - start) - duration) > 0.15:
            raise ValueError(f"Phần tử #{position}: duration không khớp end - start.")
        if start < previous_end - 0.001:
            raise ValueError(f"Phần tử #{position}: timeline bị chồng thời gian hoặc sai thứ tự.")
        previous_end = end
        entries.append({"start": start, "end": end, "duration": duration, "scene": numeric_scene})
    return entries


def _image_folder_groups(image_root):
    image_root = Path(image_root)
    if not image_root.is_dir():
        raise ValueError("Folder ảnh không tồn tại.")
    groups = {}
    for folder in image_root.iterdir():
        if folder.is_dir():
            groups.setdefault(_name_prefix(folder), []).append(folder)
    if not groups:
        raise ValueError("Folder ảnh không có folder con để ghép theo tiền tố.")
    return groups


def _scene_image_map(image_folders):
    candidates = {}
    for image_folder in image_folders:
        for path in image_folder.iterdir():
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            raw_scene = path.stem.split("_", 1)[0].strip()
            try:
                scene = int(raw_scene)
            except ValueError:
                continue
            candidates.setdefault(scene, []).append(path)
    if not candidates:
        folder_names = ", ".join(folder.name for folder in image_folders)
        raise ValueError(
            f"Folder ảnh {folder_names} không có file JPG, JPEG hoặc PNG bắt đầu bằng số scene."
        )

    def rank(path):
        stem = path.stem.casefold()
        if stem.endswith("_2k"):
            return 0, natural_key(path.name)
        return 1, natural_key(path.name)

    return {scene: sorted(paths, key=rank)[0] for scene, paths in candidates.items()}


def _validate_output_folder(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(prefix=".autoflow_write_test_", dir=folder, delete=True):
            pass
    except OSError as exc:
        raise ValueError(f"Không thể ghi vào folder output: {folder}") from exc


def prepare_timeline_video_jobs(timeline_files, audio_files, image_folder, output_folder=None):
    """Validate the complete selection before any video is rendered."""
    timelines = [Path(path) for path in dict.fromkeys(map(str, timeline_files))]
    audios = [Path(path) for path in dict.fromkeys(map(str, audio_files))]
    errors, skipped, jobs = [], [], []
    if not timelines:
        return jobs, skipped, [("Timeline", "Chưa chọn file JSON.")]
    if not audios:
        return jobs, skipped, [("MP3", "Chưa chọn file MP3.")]
    try:
        image_groups = _image_folder_groups(image_folder)
    except ValueError as exc:
        return jobs, skipped, [("Folder ảnh", str(exc))]

    timeline_groups, audio_groups = {}, {}
    for path in timelines:
        if not path.is_file() or path.suffix.lower() != ".json":
            errors.append((path.name, "File timeline không tồn tại hoặc không phải JSON."))
            continue
        timeline_groups.setdefault(_name_prefix(path), []).append(path)
    for path in audios:
        if not path.is_file() or path.suffix.lower() != ".mp3":
            errors.append((path.name, "File MP3 không tồn tại hoặc sai định dạng."))
            continue
        audio_groups.setdefault(_name_prefix(path), []).append(path)

    all_prefixes = sorted(set(timeline_groups) | set(audio_groups), key=natural_key)
    seen_outputs = set()
    for prefix in all_prefixes:
        timeline_group, audio_group = timeline_groups.get(prefix, []), audio_groups.get(prefix, [])
        label = prefix or "(không có tiền tố)"
        if len(timeline_group) != 1 or len(audio_group) != 1:
            errors.append((
                label,
                f"Cần đúng 1 timeline và 1 MP3 cùng tiền tố; hiện có {len(timeline_group)} timeline / {len(audio_group)} MP3.",
            ))
            continue
        timeline_path, audio_path = timeline_group[0], audio_group[0]
        try:
            image_folders = image_groups.get(prefix, [])
            if not image_folders:
                raise ValueError(
                    f"Không tìm thấy folder ảnh con có tiền tố ‘{timeline_path.stem.split('_', 1)[0]}’."
                )
            images = _scene_image_map(image_folders)
            entries = _read_timeline(timeline_path)
            audio_duration = float(MP3(str(audio_path)).info.length)
            if audio_duration <= 0:
                raise ValueError("MP3 không có thời lượng hợp lệ.")
            if entries[-1]["end"] > audio_duration + 0.25:
                raise ValueError(
                    f"Timeline kết thúc ở {entries[-1]['end']:.2f}s, vượt thời lượng MP3 {audio_duration:.2f}s."
                )
            missing_indexes = sorted({
                entry["scene"] for entry in entries if entry["scene"] not in images
            })
            if missing_indexes:
                preview = ", ".join(map(str, missing_indexes[:20]))
                suffix = "..." if len(missing_indexes) > 20 else ""
                raise ValueError(f"Không tìm thấy ảnh cho scene: {preview}{suffix}")
            resolved_entries = [dict(entry, image=str(images[entry["scene"]])) for entry in entries]
            destination_folder = Path(output_folder) if output_folder else audio_path.parent
            _validate_output_folder(destination_folder)
            output_path = destination_folder / f"{audio_path.stem}.mp4"
            output_key = str(output_path.resolve()).casefold()
            if output_key in seen_outputs:
                raise ValueError(f"Nhiều MP3 sẽ ghi trùng output: {output_path.name}")
            seen_outputs.add(output_key)
            if output_path.exists():
                skipped.append({
                    "timeline": timeline_path.name,
                    "mp3": audio_path.name,
                    "elapsed": 0.0,
                    "status": "BỎ QUA",
                    "result": str(output_path),
                    "detail": f"Đã có {output_path.name}",
                })
                continue
            jobs.append(TimelineVideoJob(
                timeline_path=timeline_path, audio_path=audio_path, output_path=output_path,
                audio_duration=audio_duration, entries=resolved_entries,
            ))
        except (OSError, ValueError) as exc:
            errors.append((timeline_path.name, str(exc)))
    return jobs, skipped, errors


def _random_zoom_motion(image_path, zoom):
    minimum, maximum, difference = zoom
    start = random.uniform(minimum, maximum)
    ranges = []
    if start - difference >= minimum:
        ranges.append((minimum, start - difference))
    if start + difference <= maximum:
        ranges.append((start + difference, maximum))
    end = random.uniform(*random.choice(ranges)) if ranges else (
        maximum if abs(maximum - start) > abs(start - minimum) else minimum
    )
    return start / 100.0, end / 100.0


def render_timeline_video(job, quality, fps, zoom, progress=None, cancelled=None, smooth_zoom=False,
                          motions=None):
    width, height = QUALITIES[validate_quality(quality)]
    fps = validate_fps(fps)
    zoom = parse_zoom_settings(*map(str, zoom))
    if motions is not None and len(motions) != len(job.entries):
        raise ValueError("Số chuyển động CapCut không khớp số ảnh trong timeline.")
    ffmpeg = shutil.which("ffmpeg") or r"E:\SETUP\ffmpeg-2026-03-18-git-106616f13d-full_build\bin\ffmpeg.exe"
    if not Path(ffmpeg).is_file():
        raise RuntimeError("Không tìm thấy FFmpeg. Hãy thêm ffmpeg vào PATH.")
    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".timeline_video_", dir=job.output_path.parent))
    pending = job.output_path.parent / f".{job.output_path.stem}.{uuid.uuid4().hex}.tmp"
    silent_video = temp_dir / "silent.mp4"
    encoder_arguments, encoder_label, parallel_jobs = _render_profile(str(ffmpeg))
    cpu_arguments = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19"]
    actual_encoders = set()
    total_frames = max(1, round(job.audio_duration * fps))
    spans = []
    for index, entry in enumerate(job.entries):
        start_frame = 0 if index == 0 else round(entry["start"] * fps)
        end_frame = round(job.entries[index + 1]["start"] * fps) if index + 1 < len(job.entries) else total_frames
        frame_count = max(1, end_frame - start_frame)
        motion = motions[index] if motions is not None else _random_zoom_motion(entry["image"], zoom)
        spans.append((entry["image"], frame_count, motion))

    try:
        segments = [None] * len(spans)

        def render_span(index, image_path, frames, motion):
            if cancelled and cancelled():
                raise InterruptedError("Đã dừng theo yêu cầu.")
            segment = temp_dir / f"segment_{index:05d}.mp4"
            start_zoom, end_zoom = motion
            video_filter = _motion_filter(
                width, height, start_zoom, end_zoom, frames, fps, smooth_zoom
            )
            attempts = [(encoder_arguments, encoder_label)]
            if encoder_label != "CPU x264 (Veryfast)":
                attempts.append((cpu_arguments, "CPU x264 fallback"))
            error_text = ""
            for arguments, label in attempts:
                command = [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-loop", "1",
                    "-framerate", str(fps), "-i", image_path, "-vf", video_filter,
                    "-frames:v", str(frames), "-an", *arguments, str(segment),
                ]
                result = subprocess.run(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=0x08000000,
                )
                if result.returncode == 0:
                    return index, segment, label, ""
                error_text += result.stdout.decode("utf-8", errors="replace")
            return index, segment, encoder_label, error_text

        completed = 0
        with ThreadPoolExecutor(max_workers=parallel_jobs, thread_name_prefix="timeline-render") as executor:
            futures = [
                executor.submit(render_span, index, image, frames, motion)
                for index, (image, frames, motion) in enumerate(spans, 1)
            ]
            for future in as_completed(futures):
                index, segment, used_encoder, error_text = future.result()
                if error_text:
                    for item in futures:
                        item.cancel()
                    raise RuntimeError(f"FFmpeg lỗi khi render ảnh #{index}: {error_text[-600:]}")
                segments[index - 1] = segment
                actual_encoders.add(used_encoder)
                completed += 1
                if progress:
                    progress(completed, len(spans))
                if cancelled and cancelled():
                    for item in futures:
                        item.cancel()
                    raise InterruptedError("Đã dừng theo yêu cầu.")

        concat_file = temp_dir / "concat.txt"
        with open(concat_file, "w", encoding="utf-8") as handle:
            for segment in segments:
                handle.write("file '" + str(segment).replace("'", "'\\''") + "'\n")
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_file), "-c", "copy", str(silent_video)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=0x08000000,
        )
        if result.returncode:
            raise RuntimeError("FFmpeg không thể ghép các đoạn ảnh: " + result.stdout.decode("utf-8", errors="replace")[-600:])
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent_video),
             "-i", str(job.audio_path), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
             "-f", "mp4", str(pending)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=0x08000000,
        )
        if result.returncode:
            raise RuntimeError("FFmpeg không thể ghép MP3: " + result.stdout.decode("utf-8", errors="replace")[-600:])
        os.replace(pending, job.output_path)
        return job.output_path, " + ".join(sorted(actual_encoders)) or encoder_label
    finally:
        if pending.exists():
            pending.unlink()
        shutil.rmtree(temp_dir, ignore_errors=True)
