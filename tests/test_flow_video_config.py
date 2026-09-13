import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.automations.flow_ui import (  # noqa: E402
    _duration_value,
    _video_count_value,
    _video_model_value,
    dismiss_dashboard_promos,
)
from ui.views.flow_video import (  # noqa: E402
    _task_error_tooltip,
    _task_status_for_filter,
)


def test_video_model_names_match_current_flow_options():
    assert _video_model_value("Omni Flash") == "Omni 1.1 Flash"
    assert _video_model_value("Omni 1.1 Flash") == "Omni 1.1 Flash"
    assert _video_model_value("Veo 3.1 - Lite") == "Veo 3.1 - Lite"
    assert _video_model_value("Veo 3.1 - Fast") == "Veo 3.1 - Fast"
    assert _video_model_value("Veo 3.1 - Quality") == "Veo 3.1 - Quality"
    assert (
        _video_model_value("Veo 3.1 - Lite [Lower Priority]")
        == "Veo 3.1 - Lite [Lower Priority]"
    )


def test_video_duration_uses_supported_values_and_defaults_to_8s():
    assert _duration_value("4s") == "4s"
    assert _duration_value("6 seconds") == "6s"
    assert _duration_value(8) == "8s"
    assert _duration_value("unknown") == "8s"


def test_video_count_is_limited_to_flow_range():
    assert _video_count_value(0) == 1
    assert _video_count_value("2") == 2
    assert _video_count_value(4) == 4
    assert _video_count_value(100) == 4
    assert _video_count_value(None) == 1


class _FakeButton:
    def __init__(self):
        self.clicked = False

    def is_visible(self):
        return True

    def click(self, force=False):
        self.clicked = force


class _FakeLocator:
    def __init__(self, items=()):
        self.items = list(items)

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class _PromoPage:
    def __init__(self):
        self.cookie = _FakeButton()
        self.credit = _FakeButton()

    def locator(self, selector):
        if "glue-cookie-notification-bar__accept" in selector:
            return _FakeLocator((self.cookie, self.credit))
        return _FakeLocator()

    def wait_for_timeout(self, _milliseconds):
        pass


def test_dashboard_blockers_close_before_new_project_is_visible():
    page = _PromoPage()

    assert dismiss_dashboard_promos(page) == 2
    assert page.cookie.clicked is True
    assert page.credit.clicked is True


def test_video_task_status_filter_maps_error_before_pagination():
    assert _task_status_for_filter("Lỗi") == "ERROR"
    assert _task_status_for_filter("Hoàn thành") == "COMPLETED"
    assert _task_status_for_filter("Tất cả") is None
    assert _task_status_for_filter("Đang chọn") is None


def test_video_error_tooltip_contains_google_error_and_retry_count():
    tooltip = _task_error_tooltip(
        "Google Labs Flow báo lỗi | Rất tiếc, đã xảy ra lỗi!", 1
    )
    assert "Chi tiết lỗi Google Labs" in tooltip
    assert "Đã retry: 1/3" in tooltip
    assert "Google Labs Flow báo lỗi\nRất tiếc" in tooltip
