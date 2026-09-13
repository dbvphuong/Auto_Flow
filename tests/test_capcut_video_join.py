from pathlib import Path

import pytest

from core import capcut_tools


def _probe(path, *, audio=True, width=1920, height=1080, duration=1.5, fps=30.0):
    return {
        "path": Path(path),
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": audio,
    }


def test_concat_filter_preserves_order_and_supplies_silence_for_missing_audio():
    graph = capcut_tools._concat_filter(
        [_probe("one.mp4"), _probe("two.mp4", audio=False)], 1920, 1080, 30.0
    )

    assert "[0:v:0]" in graph
    assert "[1:v:0]" in graph
    assert "[0:a:0]" in graph
    assert "[1:a:0]" not in graph
    assert "anullsrc=r=48000:cl=stereo" in graph
    assert "[join_v0][join_a0][join_v1][join_a1]concat=n=2:v=1:a=1" in graph


def test_replace_intro_offsets_child_inputs_and_keeps_source_audio(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    child = tmp_path / "child.mp4"
    source.write_bytes(b"source")
    child.write_bytes(b"child")
    captured = {}

    def fake_probe(path):
        if Path(path).name == "source.mp4":
            return _probe(path, duration=8.0, fps=25.0)
        return _probe(path, audio=False, duration=2.0, width=720, height=1280)

    def fake_encode(inputs, graph, maps, output, cancelled):
        captured.update(inputs=inputs, graph=graph, maps=maps, output=output)
        return output, "test encoder"

    monkeypatch.setattr(capcut_tools, "_probe_video", fake_probe)
    monkeypatch.setattr(capcut_tools, "_encode_joined_video", fake_encode)

    output, encoder = capcut_tools.replace_video_intro(
        source, [child], tmp_path / "result.mp4"
    )

    assert encoder == "test encoder"
    assert output.name == "result.mp4"
    assert captured["inputs"] == [source.resolve(), child.resolve()]
    assert "[1:v:0]" in captured["graph"]
    assert "trim=duration=8.000000" in captured["graph"]
    assert captured["maps"] == ["[out_v]", "0:a:0"]


def test_replace_intro_mixes_overlay_audio_without_lowering_source(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    for path in (source, first, second):
        path.write_bytes(b"video")
    captured = {}

    def fake_probe(path):
        duration = 8.0 if Path(path).name == "source.mp4" else 2.0
        return _probe(path, duration=duration, audio=True)

    def fake_encode(inputs, graph, maps, output, cancelled):
        captured.update(graph=graph, maps=maps)
        return output, "test encoder"

    monkeypatch.setattr(capcut_tools, "_probe_video", fake_probe)
    monkeypatch.setattr(capcut_tools, "_encode_joined_video", fake_encode)

    capcut_tools.replace_video_intro(
        source, [first, second], tmp_path / "result.mp4", overlay_volume=35
    )

    assert "[1:a:0]" in captured["graph"]
    assert "[2:a:0]" in captured["graph"]
    assert "[0:a:0]volume=1.000000" in captured["graph"]
    assert "[joined_a]volume=0.350000" in captured["graph"]
    assert "normalize=0[out_a]" in captured["graph"]
    assert captured["maps"] == ["[out_v]", "[out_a]"]


def test_replace_intro_zero_overlay_volume_keeps_only_source_audio(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    child = tmp_path / "child.mp4"
    source.write_bytes(b"source")
    child.write_bytes(b"child")
    captured = {}

    monkeypatch.setattr(
        capcut_tools, "_probe_video", lambda path: _probe(path, audio=True)
    )
    monkeypatch.setattr(
        capcut_tools, "_encode_joined_video",
        lambda inputs, graph, maps, output, cancelled:
            (captured.update(graph=graph, maps=maps) or (output, "test encoder")),
    )

    capcut_tools.replace_video_intro(
        source, [child], tmp_path / "result.mp4", overlay_volume=0
    )

    assert "[1:a:0]" not in captured["graph"]
    assert captured["maps"] == ["[out_v]", "0:a:0"]


@pytest.mark.parametrize("volume", [-1, 101, "loud"])
def test_replace_intro_rejects_invalid_overlay_volume(volume, tmp_path):
    with pytest.raises(ValueError, match="0.*100|số"):
        capcut_tools.replace_video_intro(
            tmp_path / "source.mp4", [], tmp_path / "result.mp4",
            overlay_volume=volume,
        )


def test_merge_requires_at_least_two_clips(tmp_path):
    clip = tmp_path / "only.mp4"
    clip.write_bytes(b"video")

    with pytest.raises(ValueError, match="ít nhất 2"):
        capcut_tools.merge_videos([clip], tmp_path / "output.mp4")


def test_join_output_cannot_overwrite_an_input(tmp_path):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    with pytest.raises(ValueError, match="không được trùng"):
        capcut_tools._validate_join_request([first, second], first, minimum=2)
