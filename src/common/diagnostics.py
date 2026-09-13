import logging
import os
import re
from datetime import datetime


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIAGNOSTICS_DIR = os.path.join(PROJECT_ROOT, "logs", "diagnostics")


def save_page_diagnostics(page, category, task_name):
    """Save browser failure artifacts outside the user's output directory."""
    safe_category = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(category)).strip("_") or "flow"
    safe_task_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(task_name)).strip("_") or "task"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base_path = os.path.join(
        DIAGNOSTICS_DIR,
        f"{safe_category}_{safe_task_name}_{timestamp}",
    )
    screenshot_path = f"{base_path}.png"
    html_path = f"{base_path}.html"

    try:
        os.makedirs(DIAGNOSTICS_DIR, exist_ok=True)
        page.screenshot(path=screenshot_path)
        with open(html_path, "w", encoding="utf-8") as html_file:
            html_file.write(page.content())
        logging.warning(
            "[Diagnostics] Đã lưu thông tin lỗi ngoài thư mục output: %s và %s",
            screenshot_path,
            html_path,
        )
        return screenshot_path, html_path
    except Exception as exc:
        logging.warning("[Diagnostics] Không thể lưu thông tin lỗi trình duyệt: %s", exc)
        return None, None
