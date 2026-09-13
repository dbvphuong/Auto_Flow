from pathlib import Path

from common import diagnostics


class FakePage:
    def screenshot(self, path):
        Path(path).write_bytes(b"png")

    def content(self):
        return "<html>failure</html>"


def test_page_diagnostics_are_saved_under_logs_not_output(tmp_path, monkeypatch):
    diagnostics_dir = tmp_path / "logs" / "diagnostics"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(diagnostics, "DIAGNOSTICS_DIR", str(diagnostics_dir))

    screenshot_path, html_path = diagnostics.save_page_diagnostics(
        FakePage(), "flow/video", "error 1"
    )

    assert Path(screenshot_path).parent == diagnostics_dir
    assert Path(html_path).parent == diagnostics_dir
    assert Path(screenshot_path).read_bytes() == b"png"
    assert Path(html_path).read_text(encoding="utf-8") == "<html>failure</html>"
    assert list(output_dir.iterdir()) == []
