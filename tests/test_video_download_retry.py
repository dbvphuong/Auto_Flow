from core.automations import video_fx


def test_classifies_real_flow_upscale_progress_message():
    text = (
        "Đang tăng độ phân giải video. Quá trình này có thể mất vài phút. "
        "Để có kết quả tốt nhất, hãy tránh bắt đầu nhiều công việc tăng độ "
        "phân giải cùng lúc."
    )

    assert video_fx._classify_upscale_status(text) == "progress"


def test_classifies_upscale_terminal_messages_before_retrying():
    assert (
        video_fx._classify_upscale_status("Đã tăng độ phân giải video")
        == "success"
    )
    assert (
        video_fx._classify_upscale_status("Không tăng độ phân giải được")
        == "failure"
    )


class _Keyboard:
    def press(self, _key):
        return None


class _Page:
    keyboard = _Keyboard()

    def wait_for_timeout(self, _milliseconds):
        return None


def test_download_retry_does_not_reload_and_lose_generated_tile(monkeypatch, tmp_path):
    page = _Page()
    calls = []
    reloads = []

    def fake_download(_page, tile, quality, _path):
        calls.append((tile, quality))
        if len(calls) < 2:
            raise RuntimeError("upscale is not ready")
        return True

    def fake_reload(_page, tile_id, tile):
        reloads.append((tile_id, tile))
        return f"tile-after-reload-{len(reloads)}"

    monkeypatch.setattr(video_fx, "_download_video", fake_download)
    monkeypatch.setattr(video_fx, "_reload_generated_tile", fake_reload)

    result = video_fx._download_video_with_retry(
        page, "original-tile", "1080p", str(tmp_path / "video.mp4"),
        attempts=2, tile_id="tile-123", reload_between_attempts=False,
    )

    assert result is True
    assert calls == [
        ("original-tile", "1080p"),
        ("original-tile", "1080p"),
    ]
    assert reloads == []


def test_1080_failure_falls_back_to_720_and_uses_720_filename(
    monkeypatch, tmp_path,
):
    calls = []

    def fake_retry(_page, _tile, quality, path, **kwargs):
        calls.append((quality, path, kwargs))
        if quality == "1080p":
            raise RuntimeError("1080 failed")
        return True

    monkeypatch.setattr(video_fx, "_download_video_with_retry", fake_retry)
    monkeypatch.setattr(
        video_fx, "_find_generated_tile", lambda _page, _id, tile, **_kw: tile
    )

    downloaded, path, tile, error = video_fx._download_configured_quality(
        _Page(), "tile", "tile-123", "1080p", str(tmp_path), "sample",
    )

    assert downloaded is True
    assert path.endswith("sample_720p.mp4")
    assert tile == "tile"
    assert error is None
    assert [call[0] for call in calls] == ["1080p", "720p"]
    assert calls[0][2]["attempts"] == 1
    assert calls[0][2]["reload_between_attempts"] is False
    assert calls[1][2]["attempts"] == 1


def test_source_fallback_path_is_720_after_upscale_and_720_menu_fail(
    monkeypatch, tmp_path,
):
    errors = {
        "1080p": RuntimeError("1080 failed"),
        "720p": RuntimeError("720 failed"),
    }

    def always_fail(_page, _tile, quality, _path, **_kwargs):
        raise errors[quality]

    monkeypatch.setattr(video_fx, "_download_video_with_retry", always_fail)
    monkeypatch.setattr(
        video_fx, "_find_generated_tile", lambda _page, _id, tile, **_kw: tile
    )

    downloaded, path, _, error = video_fx._download_configured_quality(
        _Page(), "tile", "tile-123", "1080p", str(tmp_path), "sample",
    )

    assert downloaded is False
    assert path.endswith("sample_720p.mp4")
    assert error is errors["720p"]
