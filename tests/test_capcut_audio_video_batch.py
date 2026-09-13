import os
from pathlib import Path
import subprocess

import pytest

from core import capcut_tools


class _AudioInfo:
    length = 40.0


class _Audio:
    info = _AudioInfo()


def _touch(path, content=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _probe(path, duration, audio=True):
    return {
        "path": Path(path),
        "duration": duration,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "has_audio": audio,
    }


def test_prepare_matches_copy_suffix_and_outputs_next_to_mp3(monkeypatch, tmp_path):
    monkeypatch.setattr(capcut_tools, "MP3", lambda path: _Audio())
    audio = _touch(tmp_path / "audio" / "4.Anh_full.mp3")
    video_root = tmp_path / "videos"
    first = _touch(video_root / "4.Anh_full(1)" / "2.mp4")
    second = _touch(video_root / "4.Anh_full(1)" / "10.mp4")

    jobs, skipped, errors = capcut_tools.prepare_video_audio_batch_jobs([audio], video_root)

    assert not skipped
    assert not errors
    assert len(jobs) == 1
    assert jobs[0].video_folder.name == "4.Anh_full(1)"
    assert jobs[0].video_paths == [first, second]
    assert jobs[0].output_path == audio.with_suffix(".mp4")


def test_prepare_rejects_prefix_only_match(monkeypatch, tmp_path):
    monkeypatch.setattr(capcut_tools, "MP3", lambda path: _Audio())
    audio = _touch(tmp_path / "4.Anh_full.mp3")
    _touch(tmp_path / "videos" / "4.Anh" / "1.mp4")

    jobs, skipped, errors = capcut_tools.prepare_video_audio_batch_jobs(
        [audio], tmp_path / "videos"
    )

    assert not jobs
    assert not skipped
    assert any("Không tìm thấy folder" in reason for _, reason in errors)


def test_prepare_reports_ambiguous_folders(monkeypatch, tmp_path):
    monkeypatch.setattr(capcut_tools, "MP3", lambda path: _Audio())
    audio = _touch(tmp_path / "item.mp3")
    (tmp_path / "videos" / "item").mkdir(parents=True)
    _touch(tmp_path / "videos" / "item(1)" / "1.mp4")

    jobs, skipped, errors = capcut_tools.prepare_video_audio_batch_jobs(
        [audio], tmp_path / "videos"
    )

    assert not jobs
    assert not skipped
    assert any("nhiều folder" in reason for _, reason in errors)


def test_prepare_rejects_matched_folder_without_mp4(monkeypatch, tmp_path):
    monkeypatch.setattr(capcut_tools, "MP3", lambda path: _Audio())
    audio = _touch(tmp_path / "item.mp3")
    (tmp_path / "videos" / "item(1)").mkdir(parents=True)

    jobs, skipped, errors = capcut_tools.prepare_video_audio_batch_jobs(
        [audio], tmp_path / "videos"
    )

    assert not jobs
    assert not skipped
    assert errors == [(
        "item.mp3",
        f"Folder đã khớp nhưng không có file MP4 trực tiếp bên trong: "
        f"{tmp_path / 'videos' / 'item(1)'}",
    )]


def test_prepare_skips_existing_output_without_requiring_video_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(capcut_tools, "MP3", lambda path: _Audio())
    audio = _touch(tmp_path / "item.mp3")
    _touch(audio.with_suffix(".mp4"))
    (tmp_path / "videos").mkdir()

    jobs, skipped, errors = capcut_tools.prepare_video_audio_batch_jobs(
        [audio], tmp_path / "videos"
    )

    assert not jobs
    assert not errors
    assert skipped == [("item.mp3", "Đã tồn tại: item.mp4")]


def test_prepare_ignores_unselected_extra_video_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(capcut_tools, "MP3", lambda path: _Audio())
    audio = _touch(tmp_path / "item.mp3")
    _touch(tmp_path / "videos" / "item" / "1.mp4")
    _touch(tmp_path / "videos" / "extra" / "1.mp4")

    jobs, skipped, errors = capcut_tools.prepare_video_audio_batch_jobs(
        [audio], tmp_path / "videos"
    )

    assert len(jobs) == 1
    assert not skipped
    assert not errors


def test_occurrences_keep_first_pass_order_then_fill_and_trim():
    probes = [_probe("1.mp4", 3.0), _probe("2.mp4", 4.0), _probe("3.mp4", 5.0)]

    occurrences = capcut_tools._build_video_audio_occurrences(
        probes, 15.0, capcut_tools.random.Random(5)
    )

    assert [index for index, _ in occurrences[:3]] == [0, 1, 2]
    assert sum(duration for _, duration in occurrences) == 15.0
    assert occurrences[-1][1] <= probes[occurrences[-1][0]]["duration"]
    assert occurrences[3][0] != occurrences[2][0]


def test_normalize_command_adds_pcm_silence_and_quantizes_to_frames(tmp_path):
    probe = _probe(tmp_path / "source.mp4", 2.0, audio=False)
    probe["video_duration"] = 1.01

    command, duration = capcut_tools._batch_normalize_command(
        "ffmpeg", probe, tmp_path / "clip.mov", 1920, 1080, 30.0,
        ["-c:v", "h264_qsv"], True,
    )

    assert duration == 1.0
    assert "anullsrc=r=48000:cl=stereo" in command
    assert command[command.index("-c:a") + 1] == "pcm_s16le"
    assert command[command.index("-frames:v") + 1] == "30"
    assert command[command.index("-video_track_timescale") + 1] == "90000"
    video_filter = command[command.index("-vf") + 1]
    assert video_filter.startswith("setpts=PTS-STARTPTS,tpad=stop_mode=clone")


def test_concat_manifest_uses_relative_names_and_repeats_without_copying(tmp_path):
    folder = tmp_path / "Space Isn't Empty"
    folder.mkdir()
    paths = [folder / "clip_00000.mov", folder / "clip_00001.mov"]
    manifest = folder / "schedule.ffconcat"

    capcut_tools._write_batch_concat_file(
        manifest, paths, [(0, 1.0), (1, 1.0), (0, 0.5)]
    )

    assert manifest.read_text(encoding="utf-8").splitlines() == [
        "ffconcat version 1.0",
        "file 'clip_00000.mov'",
        "file 'clip_00001.mov'",
        "file 'clip_00000.mov'",
    ]


def test_final_command_stream_copies_video_and_preserves_requested_gains(tmp_path):
    command = capcut_tools._batch_final_command(
        "ffmpeg", tmp_path / "schedule.ffconcat", tmp_path / "voice.mp3",
        tmp_path / "pending.mp4", 40.0, 35.0, True,
    )
    graph = command[command.index("-filter_complex") + 1]

    assert command[command.index("-c:v") + 1] == "copy"
    assert "volume=1.000000" in graph
    assert "volume=0.350000" in graph
    assert "normalize=0[out_a]" in graph


def test_final_command_zero_video_volume_uses_no_video_audio(tmp_path):
    command = capcut_tools._batch_final_command(
        "ffmpeg", tmp_path / "schedule.ffconcat", tmp_path / "voice.mp3",
        tmp_path / "pending.mp4", 40.0, 0.0, False,
    )
    graph = command[command.index("-filter_complex") + 1]

    assert "[0:a:0]" not in graph
    assert "volume=1.000000" in graph
    assert command[command.index("-c:v") + 1] == "copy"


def test_renderer_restarts_all_normalization_with_cpu_after_hardware_failure(
    monkeypatch, tmp_path
):
    audio = _touch(tmp_path / "voice.mp3")
    video_folder = tmp_path / "videos"
    first = _touch(video_folder / "1.mp4")
    second = _touch(video_folder / "2.mp4")
    output = tmp_path / "voice.mp4"
    job = capcut_tools.VideoAudioBatchJob(
        audio_path=audio,
        video_folder=video_folder,
        video_paths=[first, second],
        output_path=output,
        audio_duration=4.0,
    )
    probes = {
        "1.mp4": _probe(first, 1.0),
        "2.mp4": _probe(second, 1.5, audio=False),
    }
    normalization_attempts = []
    final_commands = []

    monkeypatch.setattr(capcut_tools, "_probe_video", lambda path: probes[Path(path).name])
    monkeypatch.setattr(capcut_tools, "_media_executable", lambda name: "ffmpeg")
    monkeypatch.setattr(
        capcut_tools, "_render_profile",
        lambda ffmpeg: (["-c:v", "h264_qsv"], "Intel Quick Sync", 1),
    )

    def fake_run(command, log_path, cancelled):
        destination = Path(command[-1])
        if "-f" in command and "concat" in command:
            final_commands.append(command)
            destination.write_bytes(b"final")
            return subprocess.CompletedProcess(command, 0)
        source = Path(command[command.index("-i") + 1]).name
        encoder = command[command.index("-c:v") + 1]
        assert "scale=2560:1440" in command[command.index("-vf") + 1]
        normalization_attempts.append((encoder, source))
        if encoder == "h264_qsv" and source == "2.mp4":
            return subprocess.CompletedProcess(command, 1)
        destination.write_bytes(b"normalized")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(capcut_tools, "_run_cancellable_ffmpeg", fake_run)

    result, encoder = capcut_tools.render_video_audio_batch_job(job, 10, "2K")

    assert result == output
    assert output.read_bytes() == b"final"
    assert normalization_attempts == [
        ("h264_qsv", "1.mp4"),
        ("h264_qsv", "2.mp4"),
        ("libx264", "1.mp4"),
        ("libx264", "2.mp4"),
    ]
    assert len(final_commands) == 1
    assert final_commands[0][final_commands[0].index("-c:v") + 1] == "copy"
    assert encoder == "CPU x264 fallback + stream copy"
    assert not list(tmp_path.glob(".autoflow_video_join_*"))


@pytest.mark.skipif(
    os.environ.get("AUTOFLOW_RUN_FFMPEG_TESTS") != "1",
    reason="Set AUTOFLOW_RUN_FFMPEG_TESTS=1 to run the real FFmpeg smoke test",
)
def test_low_ram_pipeline_ffmpeg_smoke_with_apostrophe_and_mixed_audio(tmp_path):
    try:
        ffmpeg = capcut_tools._media_executable("ffmpeg")
        ffprobe = capcut_tools._media_executable("ffprobe")
    except RuntimeError:
        pytest.skip("FFmpeg is not installed")
    root = tmp_path / "Space Isn't Empty"
    video_folder = root / "videos" / "sample(1)"
    video_folder.mkdir(parents=True)
    audio = root / "sample.mp3"
    first = video_folder / "1.mp4"
    second = video_folder / "2.mp4"

    commands = [
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=3.2", str(audio)],
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "color=c=red:s=320x180:d=0.8:r=24", "-f", "lavfi", "-i",
         "sine=frequency=220:duration=1.2", "-c:v", "libx264", "-pix_fmt",
         "yuv420p", "-c:a", "aac", str(first)],
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "color=c=blue:s=640x360:d=0.7:r=30", "-an", "-c:v",
         "libx264", "-pix_fmt", "yuv420p", str(second)],
    ]
    for command in commands:
        subprocess.run(command, check=True, capture_output=True)

    jobs, skipped, errors = capcut_tools.prepare_video_audio_batch_jobs(
        [audio], root / "videos"
    )
    assert not skipped and not errors
    output, _ = capcut_tools.render_video_audio_batch_job(jobs[0], 25)

    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", str(output)],
        check=True, capture_output=True, text=True,
    )
    assert abs(float(result.stdout.strip()) - 3.2) < 0.12
    assert not list(root.glob(".autoflow_video_join_*"))
