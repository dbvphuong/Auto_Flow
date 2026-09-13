import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import get_close_matches
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from mutagen.mp3 import MP3
from PIL import Image, ImageOps


QUALITIES = {
    "720P": (1280, 720),
    "1080P": (1920, 1080),
    "2K": (2560, 1440),
    "4K": (3840, 2160),
}
FPS_OPTIONS = (24, 25, 30, 50, 60)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v")


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
    media_type: str = "image"


@dataclass
class VideoAudioBatchJob:
    audio_path: Path
    video_folder: Path
    video_paths: list[Path]
    output_path: Path
    audio_duration: float


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
        raise ValueError("Độ phân giải phải là 720P, 1080P, 2K hoặc 4K.")
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

    is_clip_timeline = job.media_type == "video"
    media_key = "video" if is_clip_timeline else "image"
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
            "id": video_id, "path": str(entry[media_key]),
            "type": "video" if is_clip_timeline else "photo", "duration": duration,
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


def _filter_thread_budget(parallel_jobs):
    """Split the CPUs reserved for rendering between concurrent FFmpeg jobs."""
    logical_cpus = os.cpu_count() or 4
    usable_threads = max(1, logical_cpus // 4)
    return max(1, usable_threads // max(1, parallel_jobs))


def _limit_ffmpeg_cpu(process):
    """Reserve most CPU capacity for the UI while hardware encoding is active.

    FFmpeg's ``-threads`` only controls selected codecs and filters can still
    create extra workers. A shared Windows affinity mask keeps the combined
    load bounded even when two hardware-encoder jobs run concurrently.
    """
    if sys.platform != "win32":
        return
    logical_cpus = min(os.cpu_count() or 1, 64)
    allowed_cpus = max(1, logical_cpus // 4)
    affinity_mask = (1 << allowed_cpus) - 1
    try:
        import ctypes

        ctypes.windll.kernel32.SetProcessAffinityMask(
            int(process._handle), ctypes.c_size_t(affinity_mask)
        )
    except (AttributeError, OSError, ValueError):
        # Thread flags below remain a useful soft limit on non-standard hosts.
        pass


def _run_ffmpeg(command, *, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=None):
    """Run a render command with a hard CPU cap and low process priority."""
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    process = subprocess.Popen(
        command, stdout=stdout, stderr=stderr, creationflags=creationflags
    )
    _limit_ffmpeg_cpu(process)
    try:
        output, error = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, output, error)


def _media_executable(name):
    """Return an FFmpeg utility from PATH or the project's Windows fallback."""
    executable = shutil.which(name)
    if executable:
        return executable
    fallback = Path(
        r"E:\SETUP\ffmpeg-2026-03-18-git-106616f13d-full_build\bin"
    ) / f"{name}.exe"
    if fallback.is_file():
        return str(fallback)
    raise RuntimeError(f"Không tìm thấy {name}. Hãy thêm FFmpeg vào PATH.")


def _probe_video(path):
    """Read the stream details needed to normalize videos before joining."""
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != ".mp4":
        raise ValueError(f"File MP4 không hợp lệ: {path}")
    result = subprocess.run(
        [
            _media_executable("ffprobe"), "-v", "error", "-show_streams",
            "-show_format", "-of", "json", str(path),
        ],
        capture_output=True,
        timeout=30,
        creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Không đọc được video {path.name}: {detail or 'FFprobe báo lỗi'}")
    try:
        info = json.loads(result.stdout.decode("utf-8", errors="replace"))
        video = next(stream for stream in info.get("streams", []) if stream.get("codec_type") == "video")
        video_duration_value = video.get("duration")
        if not video_duration_value and video.get("duration_ts") and video.get("time_base"):
            video_duration_value = float(video["duration_ts"]) * float(Fraction(video["time_base"]))
        video_duration = float(video_duration_value or info.get("format", {}).get("duration"))
        duration = float(info.get("format", {}).get("duration") or video_duration)
        width, height = int(video["width"]), int(video["height"])
        rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1"
        fps = float(Fraction(rate)) if rate != "0/0" else 30.0
    except (KeyError, StopIteration, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"Video thiếu thông tin hình ảnh/thời lượng: {path.name}") from exc
    if duration <= 0 or width <= 0 or height <= 0:
        raise ValueError(f"Video có thông tin không hợp lệ: {path.name}")
    return {
        "path": path,
        "duration": duration,
        "video_duration": video_duration,
        "width": width - (width % 2),
        "height": height - (height % 2),
        "fps": min(60.0, max(1.0, fps)),
        "has_audio": any(
            stream.get("codec_type") == "audio" for stream in info.get("streams", [])
        ),
    }


def _normalized_video_filter(input_index, label, width, height, fps, duration=None):
    trim = f",trim=duration={duration:.6f}" if duration is not None else ""
    return (
        f"[{input_index}:v:0]{trim.lstrip(',') + ',' if trim else ''}"
        f"setpts=PTS-STARTPTS,fps={fps:.6f},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,format=yuv420p[{label}]"
    )


def _concat_filter(probes, width, height, fps, include_audio=True, input_offset=0):
    """Build a concat graph that tolerates mixed sizes, FPS, and missing audio."""
    filters, inputs = [], []
    for index, probe in enumerate(probes):
        input_index = index + input_offset
        duration = probe["duration"]
        video_label = f"join_v{index}"
        filters.append(_normalized_video_filter(input_index, video_label, width, height, fps, duration))
        inputs.append(f"[{video_label}]")
        if include_audio:
            audio_label = f"join_a{index}"
            if probe["has_audio"]:
                filters.append(
                    f"[{input_index}:a:0]aresample=48000:async=1:first_pts=0,"
                    "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                    f"apad,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[{audio_label}]"
                )
            else:
                filters.append(
                    "anullsrc=r=48000:cl=stereo,"
                    f"atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[{audio_label}]"
                )
            inputs.append(f"[{audio_label}]")
    outputs = "[joined_v][joined_a]" if include_audio else "[joined_v]"
    filters.append(
        "".join(inputs) + f"concat=n={len(probes)}:v=1:a={1 if include_audio else 0}{outputs}"
    )
    return ";".join(filters)


def _run_cancellable_ffmpeg(command, log_path, cancelled=None):
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    with open(log_path, "ab") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, creationflags=creationflags)
        _limit_ffmpeg_cpu(process)
        while True:
            try:
                returncode = process.wait(timeout=0.25)
                return subprocess.CompletedProcess(command, returncode)
            except subprocess.TimeoutExpired:
                if cancelled and cancelled():
                    process.kill()
                    process.wait()
                    raise InterruptedError("Đã dừng theo yêu cầu.")


def _validate_join_request(video_paths, output_path, minimum=1):
    paths = [Path(path).resolve() for path in video_paths]
    if len(paths) < minimum:
        raise ValueError(f"Hãy chọn ít nhất {minimum} file MP4.")
    output = Path(output_path).resolve()
    if output.suffix.lower() != ".mp4":
        raise ValueError("File output phải có đuôi .mp4.")
    if output in paths:
        raise ValueError("File output không được trùng với video đầu vào.")
    output.parent.mkdir(parents=True, exist_ok=True)
    return paths, output


def _encode_joined_video(input_paths, filter_graph, maps, output_path, cancelled=None):
    ffmpeg = _media_executable("ffmpeg")
    pending = output_path.parent / f".{output_path.stem}_{uuid.uuid4().hex}.part.mp4"
    log_path = output_path.parent / "capcut_video_join.log"
    encoder_arguments, encoder_label, parallel_jobs = _render_profile(ffmpeg)
    cpu_arguments = [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
        "-threads", str(_filter_thread_budget(1)),
    ]
    attempts = [(encoder_arguments, encoder_label)]
    if encoder_label != "CPU x264 (Veryfast)":
        attempts.append((cpu_arguments, "CPU x264 fallback"))
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"Inputs: {len(input_paths)}\n")
        errors = []
        for encoder, label in attempts:
            if pending.exists():
                pending.unlink()
            command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
            for path in input_paths:
                command.extend(["-i", str(path)])
            command.extend(["-filter_complex", filter_graph])
            for stream_map in maps:
                command.extend(["-map", stream_map])
            command.extend([*encoder, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(pending)])
            result = _run_cancellable_ffmpeg(command, log_path, cancelled)
            if result.returncode == 0 and pending.is_file():
                os.replace(pending, output_path)
                return output_path, label
            errors.append(label)
        raise RuntimeError(
            f"FFmpeg không thể ghép video ({', '.join(errors)}). Xem log: {log_path}"
        )
    finally:
        if pending.exists():
            pending.unlink()


def merge_videos(video_paths, output_path, progress=None, cancelled=None):
    """Join MP4 files in the supplied order into one normalized MP4."""
    paths, output = _validate_join_request(video_paths, output_path, minimum=2)
    probes = []
    for index, path in enumerate(paths, 1):
        if cancelled and cancelled():
            raise InterruptedError("Đã dừng theo yêu cầu.")
        probes.append(_probe_video(path))
        if progress:
            progress(index, len(paths) + 1)
    target = probes[0]
    graph = _concat_filter(probes, target["width"], target["height"], target["fps"], True)
    result = _encode_joined_video(paths, graph, ("[joined_v]", "[joined_a]"), output, cancelled)
    if progress:
        progress(len(paths) + 1, len(paths) + 1)
    return result


def _batch_folder_key(name):
    """Normalize Windows copy suffixes, e.g. ``name(1)`` -> ``name``."""
    return re.sub(r"\s*\(\d+\)\s*$", "", str(name)).strip().casefold()


def prepare_video_audio_batch_jobs(audio_files, video_root):
    """Validate and pair MP3 files with direct child video folders.

    Existing sibling MP4 outputs are returned as skipped before folder matching,
    because they need no further work.
    """
    audios = [Path(path).resolve() for path in dict.fromkeys(map(str, audio_files))]
    root = Path(video_root).resolve()
    jobs, skipped, errors = [], [], []
    if not audios:
        return jobs, skipped, [("MP3", "Hãy chọn ít nhất một file MP3.")]
    if not root.is_dir():
        return jobs, skipped, [(
            "Folder video",
            f"Folder video gốc không tồn tại hoặc không hợp lệ: {root}",
        )]

    folder_groups = {}
    for folder in sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: natural_key(path.name)):
        folder_groups.setdefault(_batch_folder_key(folder.name), []).append(folder)

    audio_groups = {}
    for audio_path in audios:
        if not audio_path.is_file() or audio_path.suffix.lower() != ".mp3":
            errors.append((audio_path.name, "File MP3 không hợp lệ."))
            continue
        audio_groups.setdefault(audio_path.stem.casefold(), []).append(audio_path)

    for key, matches in audio_groups.items():
        if len(matches) > 1:
            errors.append((
                matches[0].stem,
                "Có nhiều MP3 trùng tên: " + ", ".join(str(path) for path in matches),
            ))

    for key, audio_group in sorted(audio_groups.items(), key=lambda item: natural_key(item[0])):
        if len(audio_group) != 1:
            continue
        audio_path = audio_group[0]
        output_path = audio_path.with_suffix(".mp4")
        if output_path.exists():
            skipped.append((audio_path.name, f"Đã tồn tại: {output_path.name}"))
            continue
        matches = folder_groups.get(key, [])
        if not matches:
            similar_keys = get_close_matches(key, list(folder_groups), n=3, cutoff=0.35)
            similar_folders = [
                folder.name for similar_key in similar_keys
                for folder in folder_groups[similar_key]
            ]
            suggestion = (
                " Folder gần giống đang có: " + ", ".join(similar_folders) + "."
                if similar_folders else ""
            )
            errors.append((
                audio_path.name,
                f"Không tìm thấy folder video tương ứng. Cần folder tên "
                f"‘{audio_path.stem}’ hoặc ‘{audio_path.stem}(1)’ trực tiếp trong: {root}."
                f"{suggestion}",
            ))
            continue
        if len(matches) > 1:
            errors.append((
                audio_path.name,
                "Có nhiều folder cùng khớp, hãy chỉ giữ/chọn một folder: "
                + ", ".join(str(folder) for folder in matches),
            ))
            continue

        video_folder = matches[0]
        video_paths = sorted_files(video_folder, (".mp4",))
        if not video_paths:
            errors.append((
                audio_path.name,
                f"Folder đã khớp nhưng không có file MP4 trực tiếp bên trong: {video_folder}",
            ))
            continue
        try:
            audio_duration = float(MP3(str(audio_path)).info.length)
        except Exception as exc:
            errors.append((audio_path.name, f"Không đọc được thời lượng MP3: {exc}"))
            continue
        if audio_duration <= 0:
            errors.append((audio_path.name, "MP3 có thời lượng không hợp lệ."))
            continue
        jobs.append(VideoAudioBatchJob(
            audio_path=audio_path,
            video_folder=video_folder,
            video_paths=video_paths,
            output_path=output_path,
            audio_duration=audio_duration,
        ))
    return jobs, skipped, errors


def _build_video_audio_occurrences(probes, target_duration, rng=None):
    """Keep the first pass ordered, then append independently shuffled passes."""
    if not probes:
        raise ValueError("Folder video không có video hợp lệ.")
    rng = rng or random.Random()
    occurrences, elapsed = [], 0.0
    order = list(range(len(probes)))
    first_pass = True
    previous = None
    while elapsed < target_duration:
        current_order = order.copy()
        if not first_pass:
            rng.shuffle(current_order)
            if len(current_order) > 1 and current_order[0] == previous:
                swap_index = next(index for index, value in enumerate(current_order[1:], 1) if value != previous)
                current_order[0], current_order[swap_index] = current_order[swap_index], current_order[0]
        for probe_index in current_order:
            remaining = target_duration - elapsed
            if remaining <= 0:
                break
            duration = min(float(probes[probe_index]["duration"]), remaining)
            occurrences.append((probe_index, duration))
            elapsed += duration
            previous = probe_index
        first_pass = False
    return occurrences


def _batch_normalize_command(
    ffmpeg, probe, output_path, width, height, fps, encoder_arguments,
    include_audio,
):
    """Build a bounded-memory command for one normalized source clip."""
    fps_fraction = Fraction(float(fps)).limit_denominator(1001)
    fps_value = float(fps_fraction)
    fps_text = str(fps_fraction)
    source_duration = float(probe.get("video_duration") or probe["duration"])
    frames = max(1, round(source_duration * fps_value))
    duration = frames / fps_value
    video_filter = (
        f"setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={duration:.6f},"
        f"fps={fps_text},scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        "setsar=1,format=yuv420p"
    )
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(probe["path"]),
    ]
    if include_audio and not probe["has_audio"]:
        command.extend(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"])
    command.extend([
        "-map", "0:v:0", "-vf", video_filter,
        "-frames:v", str(frames), "-fps_mode", "cfr",
        *encoder_arguments, "-pix_fmt", "yuv420p",
        "-g", str(max(1, round(fps_value * 2))),
        "-video_track_timescale", "90000",
    ])
    if include_audio:
        audio_input = "0:a:0" if probe["has_audio"] else "1:a:0"
        command.extend([
            "-map", audio_input,
            "-af", (
                "aresample=48000:async=1:first_pts=0,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"apad,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS"
            ),
            "-c:a", "pcm_s16le",
        ])
    else:
        command.append("-an")
    command.extend([
        "-t", f"{duration:.6f}", "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart", str(output_path),
    ])
    return command, duration


def _write_batch_concat_file(path, normalized_paths, occurrences):
    """Write an ffconcat schedule; repeated entries do not duplicate media."""
    lines = ["ffconcat version 1.0"]
    for probe_index, _ in occurrences:
        media_path = normalized_paths[probe_index].resolve()
        try:
            value = media_path.relative_to(path.parent.resolve()).as_posix()
        except ValueError:
            value = str(media_path).replace("\\", "/")
        value = value.replace("'", "'\\''")
        lines.append(f"file '{value}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _batch_final_command(
    ffmpeg, concat_path, audio_path, pending, duration, video_volume,
    include_video_audio,
):
    """Stream-copy scheduled video and encode only the final mixed audio."""
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_path),
        "-i", str(audio_path), "-map", "0:v:0", "-c:v", "copy",
    ]
    main_audio = (
        "[1:a:0]aresample=48000:async=1:first_pts=0,"
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        f"volume=1.000000,apad,atrim=duration={duration:.6f},"
        "asetpts=PTS-STARTPTS[main_a]"
    )
    if include_video_audio:
        graph = (
            main_audio + ";"
            "[0:a:0]aresample=48000:async=1:first_pts=0,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"volume={video_volume / 100.0:.6f},apad,atrim=duration={duration:.6f},"
            "asetpts=PTS-STARTPTS[video_a];"
            "[main_a][video_a]amix=inputs=2:duration=first:dropout_transition=0:"
            "normalize=0[out_a]"
        )
    else:
        graph = main_audio + ";[main_a]anull[out_a]"
    command.extend([
        "-filter_complex", graph, "-map", "[out_a]",
        "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.6f}",
        "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(pending),
    ])
    return command


def render_video_audio_batch_job(
    job, video_volume=0, quality="1080P", progress=None, cancelled=None,
):
    """Normalize unique clips sequentially, then stream-copy the shuffled schedule."""
    try:
        video_volume = float(video_volume)
    except (TypeError, ValueError) as exc:
        raise ValueError("Âm lượng video phải là số từ 0 đến 100%.") from exc
    if not 0 <= video_volume <= 100:
        raise ValueError("Âm lượng video phải nằm trong khoảng 0 đến 100%.")
    if job.output_path.exists():
        return job.output_path, None

    probes = []
    for path in job.video_paths:
        if cancelled and cancelled():
            raise InterruptedError("Đã dừng theo yêu cầu.")
        probes.append(_probe_video(path))
    target = probes[0]
    width, height = QUALITIES[validate_quality(quality)]
    fps = target["fps"]
    include_video_audio = video_volume > 0 and any(probe["has_audio"] for probe in probes)
    ffmpeg = _media_executable("ffmpeg")
    encoder_arguments, encoder_label, _ = _render_profile(ffmpeg)
    cpu_arguments = [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
        "-threads", str(_filter_thread_budget(1)),
    ]
    attempts = [(encoder_arguments, encoder_label)]
    if encoder_label != "CPU x264 (Veryfast)":
        attempts.append((cpu_arguments, "CPU x264 fallback"))

    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".autoflow_video_join_", dir=job.output_path.parent))
    pending = job.output_path.parent / f".{job.output_path.stem}_{uuid.uuid4().hex}.part.mp4"
    concat_path = temp_dir / "schedule.ffconcat"
    log_path = job.output_path.parent / "capcut_video_audio_batch.log"
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"MP3: {job.audio_path}\nVideo folder: {job.video_folder}\n")

        normalized_paths = [temp_dir / f"clip_{index:05d}.mov" for index in range(len(probes))]
        normalized_probes = None
        actual_encoder = None
        failed_encoders = []
        for encoder, label in attempts:
            for path in normalized_paths:
                if path.exists():
                    path.unlink()
            current_probes = []
            for index, (probe, normalized_path) in enumerate(zip(probes, normalized_paths), 1):
                if cancelled and cancelled():
                    raise InterruptedError("Đã dừng theo yêu cầu.")
                command, normalized_duration = _batch_normalize_command(
                    ffmpeg, probe, normalized_path, width, height, fps,
                    encoder, include_video_audio,
                )
                result = _run_cancellable_ffmpeg(command, log_path, cancelled)
                if (
                    result.returncode or not normalized_path.is_file()
                    or normalized_path.stat().st_size <= 0
                ):
                    failed_encoders.append(f"{label} tại clip {probe['path'].name}")
                    break
                current_probes.append(dict(probe, duration=normalized_duration, path=normalized_path))
                if progress:
                    progress(index, len(probes) + 1)
            else:
                normalized_probes = current_probes
                actual_encoder = label
                break
        if normalized_probes is None:
            raise RuntimeError(
                "Không thể chuẩn hóa video bằng " + ", ".join(failed_encoders)
                + f". Xem log chi tiết: {log_path}"
            )

        rng = random.Random(f"{job.audio_path.stem}|{job.audio_duration:.6f}")
        occurrences = _build_video_audio_occurrences(
            normalized_probes, job.audio_duration, rng,
        )
        _write_batch_concat_file(concat_path, normalized_paths, occurrences)
        command = _batch_final_command(
            ffmpeg, concat_path, job.audio_path, pending, job.audio_duration,
            video_volume, include_video_audio,
        )
        result = _run_cancellable_ffmpeg(command, log_path, cancelled)
        if result.returncode or not pending.is_file() or pending.stat().st_size <= 0:
            raise RuntimeError(f"FFmpeg không thể ghép video cuối. Xem log chi tiết: {log_path}")
        os.replace(pending, job.output_path)
        if progress:
            progress(len(probes) + 1, len(probes) + 1)
        return job.output_path, f"{actual_encoder} + stream copy"
    finally:
        if pending.exists():
            pending.unlink()
        shutil.rmtree(temp_dir, ignore_errors=True)


def replace_video_intro(
    source_path, video_paths, output_path, progress=None, cancelled=None,
    overlay_volume=100,
):
    """Replace source visuals and optionally mix sequential overlay clip audio."""
    try:
        overlay_volume = float(overlay_volume)
    except (TypeError, ValueError) as exc:
        raise ValueError("Âm lượng video ghi đè phải là số từ 0 đến 100%.") from exc
    if not 0 <= overlay_volume <= 100:
        raise ValueError("Âm lượng video ghi đè phải nằm trong khoảng 0 đến 100%.")

    source = Path(source_path).resolve()
    if not source.is_file() or source.suffix.lower() != ".mp4":
        raise ValueError("Hãy chọn video nguồn MP4 hợp lệ.")
    paths, output = _validate_join_request(video_paths, output_path, minimum=1)
    if output == source:
        raise ValueError("File output không được ghi đè trực tiếp lên video nguồn.")
    source_probe = _probe_video(source)
    probes = []
    for index, path in enumerate(paths, 1):
        if cancelled and cancelled():
            raise InterruptedError("Đã dừng theo yêu cầu.")
        probes.append(_probe_video(path))
        if progress:
            progress(index, len(paths) + 1)
    width, height, fps = source_probe["width"], source_probe["height"], source_probe["fps"]
    mix_overlay_audio = overlay_volume > 0 and any(probe["has_audio"] for probe in probes)
    cover_graph = _concat_filter(
        probes, width, height, fps,
        include_audio=mix_overlay_audio, input_offset=1,
    )
    base_filter = _normalized_video_filter(0, "source_v", width, height, fps)
    graph_parts = [
        f"{base_filter};{cover_graph};"
        "[source_v][joined_v]overlay=0:0:eof_action=pass:repeatlast=0:shortest=0,"
        f"trim=duration={source_probe['duration']:.6f},setpts=PTS-STARTPTS[out_v]"
    ]
    maps = ["[out_v]"]
    if source_probe["has_audio"] and mix_overlay_audio:
        volume_factor = overlay_volume / 100.0
        graph_parts.extend([
            f"[0:a:0]volume=1.000000,apad,atrim=duration={source_probe['duration']:.6f},"
            "asetpts=PTS-STARTPTS[source_a]",
            f"[joined_a]volume={volume_factor:.6f},apad,"
            f"atrim=duration={source_probe['duration']:.6f},asetpts=PTS-STARTPTS[overlay_a]",
            "[source_a][overlay_a]amix=inputs=2:duration=first:dropout_transition=0:"
            "normalize=0[out_a]",
        ])
        maps.append("[out_a]")
    elif source_probe["has_audio"]:
        maps.append("0:a:0")
    elif mix_overlay_audio:
        volume_factor = overlay_volume / 100.0
        graph_parts.append(
            f"[joined_a]volume={volume_factor:.6f},apad,"
            f"atrim=duration={source_probe['duration']:.6f},asetpts=PTS-STARTPTS[out_a]"
        )
        maps.append("[out_a]")
    graph = ";".join(graph_parts)
    result = _encode_joined_video([source, *paths], graph, maps, output, cancelled)
    if progress:
        progress(len(paths) + 1, len(paths) + 1)
    return result


def _motion_thread_budget():
    """OpenCV reaches peak throughput before using all cores on this workload."""
    return max(1, min(4, (os.cpu_count() or 4) // 4))


def _prepare_motion_frame(image_path, width, height):
    """Decode and cover-crop a still image once instead of once per video frame."""
    import numpy as np

    with Image.open(image_path) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        scale = max(width / source.width, height / source.height)
        scaled_width = max(width, round(source.width * scale))
        scaled_height = max(height, round(source.height * scale))
        source = source.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)
        left = (scaled_width - width) // 2
        top = (scaled_height - height) // 2
        source = source.crop((left, top, left + width, top + height))
        return np.ascontiguousarray(np.asarray(source, dtype=np.uint8))


def _render_affine_motion(
    ffmpeg, image_path, output_path, width, height, start_zoom, end_zoom,
    frames, fps, encoder_arguments, centered=False, audio_path=None,
    cancelled=None,
):
    """Render continuous sub-pixel zoom with OpenCV and stream it to FFmpeg.

    A uniform affine transform is substantially cheaper than FFmpeg's general
    four-corner perspective filter. The source is resized only once, and the
    encoder still runs in hardware through Quick Sync when available.
    """
    import cv2
    import numpy as np

    cv2.setNumThreads(_motion_thread_budget())
    base_frame = _prepare_motion_frame(image_path, width, height)
    yuv = cv2.cvtColor(base_frame, cv2.COLOR_RGB2YUV_I420).reshape(-1)
    luma_size = width * height
    chroma_size = luma_size // 4
    base_y = yuv[:luma_size].reshape(height, width)
    base_u = yuv[luma_size:luma_size + chroma_size].reshape(height // 2, width // 2)
    base_v = yuv[luma_size + chroma_size:].reshape(height // 2, width // 2)
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pixel_format", "nv12",
        "-video_size", f"{width}x{height}", "-framerate", str(fps),
        "-i", "pipe:0",
    ]
    if audio_path is not None:
        command.extend(["-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0"])
    command.extend(["-frames:v", str(frames), *encoder_arguments])
    if audio_path is not None:
        command.extend(["-c:a", "aac", "-b:a", "192k", "-shortest"])
    command.append(str(output_path))

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, creationflags=creationflags,
    )
    _limit_ffmpeg_cpu(process)
    start_zoom = max(1.0, float(start_zoom))
    end_zoom = max(1.0, float(end_zoom))
    anchor_x = width / 2 if centered else 0.0
    anchor_y = height / 2 if centered else 0.0
    error_output = b""
    try:
        for index in range(frames):
            if cancelled and cancelled():
                raise InterruptedError("ÄÃ£ dá»«ng theo yÃªu cáº§u.")
            progress = index / (frames - 1) if frames > 1 else 0.0
            zoom = start_zoom + (end_zoom - start_zoom) * progress
            matrix = np.array([
                [zoom, 0.0, (1.0 - zoom) * anchor_x],
                [0.0, zoom, (1.0 - zoom) * anchor_y],
            ], dtype=np.float32)
            chroma_matrix = matrix.copy()
            chroma_matrix[:, 2] *= 0.5
            frame_y = cv2.warpAffine(
                base_y, matrix, (width, height), flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
            frame_u = cv2.warpAffine(
                base_u, chroma_matrix, (width // 2, height // 2),
                flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE,
            )
            frame_v = cv2.warpAffine(
                base_v, chroma_matrix, (width // 2, height // 2),
                flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE,
            )
            frame_uv = np.empty((height // 2, width), dtype=np.uint8)
            frame_uv[:, 0::2] = frame_u
            frame_uv[:, 1::2] = frame_v
            process.stdin.write(frame_y.data)
            process.stdin.write(frame_uv.data)
        process.stdin.close()
        process.stdin = None
        error_output, _ = process.communicate()
    except InterruptedError:
        process.kill()
        process.communicate()
        raise
    except (BrokenPipeError, OSError):
        if process.stdin:
            process.stdin.close()
            process.stdin = None
        error_output, _ = process.communicate()
    except BaseException:
        process.kill()
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, error_output, None)


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
    # OpenCV already parallelizes each affine transform; multiple simultaneous
    # raw-frame streams only contend for memory bandwidth and do not render faster.
    parallel_jobs = 1
    filter_threads = _filter_thread_budget(parallel_jobs)
    cpu_arguments = [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
        "-threads", str(filter_threads),
    ]
    actual_encoders = set()
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(
                f"Encoder: {encoder_label}\nFPS: {fps}\n"
                f"Motion engine: OpenCV affine sub-pixel\n"
                f"Parallel segments: {parallel_jobs}\nOpenCV threads: {_motion_thread_budget()}\n"
            )
            segments = [None] * len(pairs)

            def render_segment(index, pair, motion):
                if cancelled and cancelled():
                    raise InterruptedError("Đã dừng theo yêu cầu.")
                segment = temp_dir / f"segment_{index:04d}.mp4"
                frames = max(1, round(pair["duration"] / 1_000_000 * fps))
                start, end = motion
                attempts = [(encoder_arguments, encoder_label)]
                if encoder_label != "CPU x264 (Veryfast)":
                    attempts.append((cpu_arguments, "CPU x264 fallback"))
                errors = []
                for current_arguments, current_label in attempts:
                    result = _render_affine_motion(
                        ffmpeg, pair["img_path"], segment, width, height,
                        start, end, frames, fps, current_arguments,
                        centered=smooth_zoom, audio_path=pair["mp3_path"],
                        cancelled=cancelled,
                    )
                    if result.returncode == 0:
                        return index, segment, "", f"{current_label} + OpenCV affine"
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
            result = _run_ffmpeg(
                [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", "-f", "mp4", str(pending)],
                stdout=log,
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
    path = Path(path)
    # Folder names may legitimately contain dots (for example
    # ``5.Anh_full_prompt_anh``). Path.stem treats everything after the final
    # dot as a suffix even for a directory, reducing that name to ``5``.
    # Only strip a suffix from files; preserve the complete directory name.
    name = path.name if path.is_dir() else path.stem
    return name.split("_", 1)[0].strip().casefold()


def _name_stem(path):
    """Return the complete filename without its extension for exact media pairing."""
    return Path(path).stem.strip().casefold()


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
    previous_end = None
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
        if previous_end is not None and start < previous_end:
            start = previous_end
            if end <= start:
                raise ValueError(
                    f"Phần tử #{position}: end phải lớn hơn end của phần tử trước ({previous_end:g})."
                )
            duration = end - start
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


def _scene_video_map(video_folder):
    video_folder = Path(video_folder)
    if not video_folder.is_dir():
        raise ValueError("Folder video không tồn tại.")
    candidates = {}
    for path in video_folder.iterdir():
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        match = re.match(r"^(\d+)(?:\D|$)", path.stem)
        if match:
            candidates.setdefault(int(match.group(1)), []).append(path)
    if not candidates:
        raise ValueError(
            "Folder video không có clip MP4/MOV/MKV/AVI/WEBM/M4V bắt đầu bằng số scene."
        )

    def rank(path):
        stem = path.stem.casefold()
        quality = (
            0 if re.search(r"(?:^|[_-])(?:4k|2160p?)(?:[_-]|$)", stem) else
            1 if re.search(r"(?:^|[_-])(?:2k|1440p?)(?:[_-]|$)", stem) else
            2 if re.search(r"(?:^|[_-])1080p?(?:[_-]|$)", stem) else
            3 if re.search(r"(?:^|[_-])720p?(?:[_-]|$)", stem) else 4
        )
        return quality, natural_key(path.name)

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
        timeline_groups.setdefault(_name_stem(path), []).append(path)
    for path in audios:
        if not path.is_file() or path.suffix.lower() != ".mp3":
            errors.append((path.name, "File MP3 không tồn tại hoặc sai định dạng."))
            continue
        audio_groups.setdefault(_name_stem(path), []).append(path)

    all_names = sorted(set(timeline_groups) | set(audio_groups), key=natural_key)
    seen_outputs = set()
    for name in all_names:
        timeline_group, audio_group = timeline_groups.get(name, []), audio_groups.get(name, [])
        label = name or "(tên trống)"
        if len(timeline_group) != 1 or len(audio_group) != 1:
            errors.append((
                label,
                f"Cần đúng 1 timeline JSON và 1 MP3 cùng tên (chỉ khác phần mở rộng); "
                f"hiện có {len(timeline_group)} timeline / {len(audio_group)} MP3.",
            ))
            continue
        timeline_path, audio_path = timeline_group[0], audio_group[0]
        try:
            image_prefix = _name_prefix(timeline_path)
            image_folders = image_groups.get(image_prefix, [])
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


def prepare_timeline_clip_jobs(timeline_files, audio_files, video_folder, output_folder=None):
    """Validate JSON/MP3 pairs and resolve direct video clips by scene number."""
    timelines = [Path(path) for path in dict.fromkeys(map(str, timeline_files))]
    audios = [Path(path) for path in dict.fromkeys(map(str, audio_files))]
    errors, skipped, jobs = [], [], []
    if not timelines:
        return jobs, skipped, [("Timeline", "Chưa chọn file JSON.")]
    if not audios:
        return jobs, skipped, [("MP3", "Chưa chọn file MP3.")]
    try:
        videos = _scene_video_map(video_folder)
    except ValueError as exc:
        return jobs, skipped, [("Folder video", str(exc))]

    timeline_groups, audio_groups = {}, {}
    for path in timelines:
        if not path.is_file() or path.suffix.lower() != ".json":
            errors.append((path.name, "File timeline không tồn tại hoặc không phải JSON."))
            continue
        timeline_groups.setdefault(_name_stem(path), []).append(path)
    for path in audios:
        if not path.is_file() or path.suffix.lower() != ".mp3":
            errors.append((path.name, "File MP3 không tồn tại hoặc sai định dạng."))
            continue
        audio_groups.setdefault(_name_stem(path), []).append(path)

    all_names = sorted(set(timeline_groups) | set(audio_groups), key=natural_key)
    seen_outputs = set()
    for name in all_names:
        timeline_group, audio_group = timeline_groups.get(name, []), audio_groups.get(name, [])
        label = name or "(tên trống)"
        if len(timeline_group) != 1 or len(audio_group) != 1:
            errors.append((
                label,
                f"Cần đúng 1 timeline JSON và 1 MP3 cùng tên (chỉ khác phần mở rộng); "
                f"hiện có {len(timeline_group)} timeline / {len(audio_group)} MP3.",
            ))
            continue
        timeline_path, audio_path = timeline_group[0], audio_group[0]
        try:
            entries = _read_timeline(timeline_path)
            audio_duration = float(MP3(str(audio_path)).info.length)
            if audio_duration <= 0:
                raise ValueError("MP3 không có thời lượng hợp lệ.")
            if entries[-1]["end"] > audio_duration + 0.25:
                raise ValueError(
                    f"Timeline kết thúc ở {entries[-1]['end']:.2f}s, vượt thời lượng MP3 {audio_duration:.2f}s."
                )
            missing_indexes = sorted({
                entry["scene"] for entry in entries if entry["scene"] not in videos
            })
            if missing_indexes:
                preview = ", ".join(map(str, missing_indexes[:20]))
                suffix = "..." if len(missing_indexes) > 20 else ""
                raise ValueError(f"Không tìm thấy video cho scene: {preview}{suffix}")
            resolved_entries = [dict(entry, video=str(videos[entry["scene"]])) for entry in entries]
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
                audio_duration=audio_duration, entries=resolved_entries, media_type="video",
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
    if job.media_type == "video":
        return render_timeline_clip_video(
            job, quality, fps, progress=progress, cancelled=cancelled
        )
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
    parallel_jobs = 1
    filter_threads = _filter_thread_budget(parallel_jobs)
    cpu_arguments = [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
        "-threads", str(filter_threads),
    ]
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
            attempts = [(encoder_arguments, encoder_label)]
            if encoder_label != "CPU x264 (Veryfast)":
                attempts.append((cpu_arguments, "CPU x264 fallback"))
            error_text = ""
            for arguments, label in attempts:
                result = _render_affine_motion(
                    ffmpeg, image_path, segment, width, height,
                    start_zoom, end_zoom, frames, fps, arguments,
                    centered=smooth_zoom, cancelled=cancelled,
                )
                if result.returncode == 0:
                    return index, segment, f"{label} + OpenCV affine", ""
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
        result = _run_ffmpeg(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_file), "-c", "copy", str(silent_video)],
        )
        if result.returncode:
            raise RuntimeError("FFmpeg không thể ghép các đoạn ảnh: " + result.stdout.decode("utf-8", errors="replace")[-600:])
        result = _run_ffmpeg(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent_video),
             "-i", str(job.audio_path), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
             "-f", "mp4", str(pending)],
        )
        if result.returncode:
            raise RuntimeError("FFmpeg không thể ghép MP3: " + result.stdout.decode("utf-8", errors="replace")[-600:])
        os.replace(pending, job.output_path)
        return job.output_path, " + ".join(sorted(actual_encoders)) or encoder_label
    finally:
        if pending.exists():
            pending.unlink()
        shutil.rmtree(temp_dir, ignore_errors=True)


def _video_segment_command(ffmpeg, input_path, output_path, width, height, frames, fps,
                           encoder_arguments, filter_threads):
    """Build a deterministic no-motion command that loops, scales and trims one clip."""
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height},setsar=1,fps={fps}"
    )
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1",
        "-i", str(input_path), "-map", "0:v:0", "-an", "-vf", video_filter,
        "-frames:v", str(frames), *encoder_arguments,
        "-threads", str(filter_threads), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output_path),
    ]


def render_timeline_clip_video(job, quality, fps, progress=None, cancelled=None):
    """Render numbered source clips on the JSON timeline without image motion effects."""
    width, height = QUALITIES[validate_quality(quality)]
    fps = validate_fps(fps)
    ffmpeg = shutil.which("ffmpeg") or r"E:\SETUP\ffmpeg-2026-03-18-git-106616f13d-full_build\bin\ffmpeg.exe"
    if not Path(ffmpeg).is_file():
        raise RuntimeError("Không tìm thấy FFmpeg. Hãy thêm ffmpeg vào PATH.")
    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".timeline_clips_", dir=job.output_path.parent))
    pending = job.output_path.parent / f".{job.output_path.stem}.{uuid.uuid4().hex}.tmp"
    silent_video = temp_dir / "silent.mp4"
    encoder_arguments, encoder_label, _ = _render_profile(str(ffmpeg))
    filter_threads = _filter_thread_budget(1)
    cpu_arguments = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19"]
    actual_encoders = set()
    total_frames = max(1, round(job.audio_duration * fps))
    spans = []
    for index, entry in enumerate(job.entries):
        start_frame = 0 if index == 0 else round(entry["start"] * fps)
        end_frame = (
            round(job.entries[index + 1]["start"] * fps)
            if index + 1 < len(job.entries) else total_frames
        )
        spans.append((entry["video"], max(1, end_frame - start_frame)))

    try:
        segments = []
        for index, (video_path, frames) in enumerate(spans, 1):
            if cancelled and cancelled():
                raise InterruptedError("Đã dừng theo yêu cầu.")
            segment = temp_dir / f"segment_{index:05d}.mp4"
            attempts = [(encoder_arguments, encoder_label)]
            if encoder_label != "CPU x264 (Veryfast)":
                attempts.append((cpu_arguments, "CPU x264 fallback"))
            error_text = ""
            for arguments, label in attempts:
                result = _run_ffmpeg(_video_segment_command(
                    str(ffmpeg), video_path, segment, width, height, frames, fps,
                    arguments, filter_threads,
                ))
                if result.returncode == 0:
                    actual_encoders.add(label)
                    segments.append(segment)
                    break
                error_text += result.stdout.decode("utf-8", errors="replace")
            else:
                raise RuntimeError(f"FFmpeg lỗi khi xử lý video scene #{index}: {error_text[-600:]}")
            if progress:
                progress(index, len(spans))

        concat_file = temp_dir / "concat.txt"
        with open(concat_file, "w", encoding="utf-8") as handle:
            for segment in segments:
                handle.write("file '" + str(segment).replace("'", "'\\''") + "'\n")
        result = _run_ffmpeg([
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
            "-safe", "0", "-i", str(concat_file), "-c", "copy", str(silent_video),
        ])
        if result.returncode:
            raise RuntimeError(
                "FFmpeg không thể ghép các đoạn video: "
                + result.stdout.decode("utf-8", errors="replace")[-600:]
            )
        result = _run_ffmpeg([
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent_video),
            "-i", str(job.audio_path), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
            "-f", "mp4", str(pending),
        ])
        if result.returncode:
            raise RuntimeError(
                "FFmpeg không thể ghép MP3: "
                + result.stdout.decode("utf-8", errors="replace")[-600:]
            )
        os.replace(pending, job.output_path)
        return job.output_path, " + ".join(sorted(actual_encoders)) or encoder_label
    finally:
        if pending.exists():
            pending.unlink()
        shutil.rmtree(temp_dir, ignore_errors=True)
