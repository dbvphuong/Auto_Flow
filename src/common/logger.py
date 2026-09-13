import logging
import os
import threading
import time
from collections import deque
from datetime import datetime


_HISTORY_LIMIT = 1000
_history = deque(maxlen=_HISTORY_LIMIT)
_subscribers = set()
_state_lock = threading.RLock()
_log_file_path = None


class FlushFileHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()


class UserFacingFilter(logging.Filter):
    """Keep normal logs useful: explicit activity and actionable errors."""

    def filter(self, record):
        if record.getMessage().startswith("[Browser Diagnostics]"):
            return bool(getattr(record, "user_facing", False))
        return record.levelno >= logging.ERROR or bool(
            getattr(record, "user_facing", False)
        )


class DuplicateFilter(logging.Filter):
    """Hide repeated messages emitted in a tight polling/retry loop."""

    def __init__(self, interval=5.0):
        super().__init__()
        self.interval = interval
        self._last_seen = {}
        self._lock = threading.Lock()

    def filter(self, record):
        key = (record.levelno, record.getMessage())
        now = time.monotonic()
        with self._lock:
            previous = self._last_seen.get(key)
            self._last_seen[key] = now
            if len(self._last_seen) > 500:
                cutoff = now - self.interval
                self._last_seen = {
                    item: seen for item, seen in self._last_seen.items() if seen >= cutoff
                }
        return previous is None or now - previous >= self.interval


class RealtimeLogHandler(logging.Handler):
    """Publish filtered log records to the GUI without importing PyQt here."""

    def emit(self, record):
        try:
            entry = {
                "time": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                "level": record.levelname,
                "message": record.getMessage(),
            }
            with _state_lock:
                _history.append(entry)
                subscribers = tuple(_subscribers)
            for callback in subscribers:
                try:
                    callback(entry.copy())
                except Exception:
                    # A closed GUI must never break worker logging.
                    continue
        except Exception:
            self.handleError(record)


def activity(message, *args):
    """Write one concise, user-visible workflow milestone."""
    logging.getLogger("app.activity").info(
        message, *args, extra={"user_facing": True}
    )


def warning(message, *args):
    """Write a recoverable problem that the user should know about."""
    logging.getLogger("app.activity").warning(
        message, *args, extra={"user_facing": True}
    )


def progress(scope, current, total, detail=None):
    """Write a consistent current/total progress line."""
    suffix = f" - {detail}" if detail else ""
    activity("[%s] Tiến độ: %s/%s%s", scope, current, total, suffix)


def subscribe(callback, replay=True):
    """Subscribe to realtime entries; optionally return the current history."""
    with _state_lock:
        _subscribers.add(callback)
        return [entry.copy() for entry in _history] if replay else []


def unsubscribe(callback):
    with _state_lock:
        _subscribers.discard(callback)


def get_log_file_path():
    return _log_file_path


def clean_old_logs(logs_dir, days=7):
    """Remove session and diagnostic logs older than the retention period."""
    try:
        if not os.path.exists(logs_dir):
            return
        cutoff = time.time() - (days * 24 * 60 * 60)
        folders = (logs_dir, os.path.join(logs_dir, "diagnostics"))
        for folder in folders:
            if not os.path.isdir(folder):
                continue
            for filename in os.listdir(folder):
                if folder == logs_dir and not (
                    filename.startswith("session_") and filename.endswith(".log")
                ):
                    continue
                file_path = os.path.join(folder, filename)
                try:
                    if os.path.isfile(file_path) and os.path.getmtime(file_path) < cutoff:
                        os.remove(file_path)
                except OSError as exc:
                    logging.warning("Không thể dọn log cũ %s: %s", filename, exc)
    except OSError as exc:
        logging.warning("Không thể dọn thư mục log: %s", exc)


def _configured_handler(handler, formatter):
    handler.setFormatter(formatter)
    handler.addFilter(UserFacingFilter())
    handler.addFilter(DuplicateFilter())
    return handler


def setup_logger():
    """Configure one concise log file, console stream and realtime GUI feed."""
    global _log_file_path

    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(src_dir)
    logs_dir = os.path.join(project_root, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    session_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_file_path = os.path.join(logs_dir, f"session_{session_time}.log")
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    realtime = RealtimeLogHandler()
    realtime.addFilter(UserFacingFilter())
    realtime.addFilter(DuplicateFilter())

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in tuple(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    root.addHandler(_configured_handler(
        FlushFileHandler(_log_file_path, encoding="utf-8"), formatter
    ))
    root.addHandler(_configured_handler(logging.StreamHandler(), formatter))
    root.addHandler(realtime)

    activity("Ứng dụng đã khởi động")
    threading.Thread(
        target=clean_old_logs, args=(logs_dir, 7), daemon=True, name="log-cleanup"
    ).start()
    return _log_file_path
