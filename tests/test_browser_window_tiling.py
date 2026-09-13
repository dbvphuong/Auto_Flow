from core.browser_manager import apply_browser_mode_args, calculate_tiled_window_bounds
from core.workers import AutomationWorker
from core.system_config import (
    CHROME_RUN_MODE_HEADLESS,
    CHROME_RUN_MODE_MINIMIZED,
    CHROME_RUN_MODE_VISIBLE,
    get_chrome_run_mode,
)


def test_five_windows_split_the_work_area_into_equal_columns():
    work_area = (0, 0, 1000, 800)
    assert [
        calculate_tiled_window_bounds(slot, 5, work_area)
        for slot in range(5)
    ] == [
        (0, 0, 200, 800),
        (200, 0, 200, 800),
        (400, 0, 200, 800),
        (600, 0, 200, 800),
        (800, 0, 200, 800),
    ]


def test_flow_worker_only_tiles_chrome_when_browser_is_visible():
    worker = AutomationWorker(1, window_slot=3, window_count=5)
    assert worker.visible_window_slot(True) == (3, 5)
    assert worker.visible_window_slot(False) is None


def test_chrome_run_mode_migrates_the_old_checkbox_setting():
    assert get_chrome_run_mode({"show_chrome_when_running": True}) == CHROME_RUN_MODE_VISIBLE
    assert get_chrome_run_mode({"show_chrome_when_running": False}) == CHROME_RUN_MODE_MINIMIZED


def test_chrome_run_mode_accepts_headless():
    assert get_chrome_run_mode({"chrome_run_mode": "headless"}) == CHROME_RUN_MODE_HEADLESS


def test_headless_mode_uses_native_chrome_headless_flag_without_window_position():
    args = apply_browser_mode_args([], CHROME_RUN_MODE_HEADLESS, (0, 0, 500, 800))
    assert "--headless=new" in args
    assert not any(arg.startswith("--window-position=") for arg in args)


def test_minimized_mode_keeps_the_previous_offscreen_behavior():
    args = apply_browser_mode_args([], CHROME_RUN_MODE_MINIMIZED)
    assert "--start-minimized" in args
    assert "--window-position=-32000,-32000" in args
