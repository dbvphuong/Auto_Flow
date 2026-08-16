from PyQt6.QtCore import QThread, pyqtSignal
from playwright.sync_api import sync_playwright
import json
import logging

from data.database import SessionLocal
from data.models import Account, Task, GeminiBatch
from common.gemini_languages import LANGUAGE_BY_COUNTRY
from .automations.gemini import run_gemini, GeminiStopped, GeminiProUnavailable
from .automations.image_fx import run_image_fx
from .automations.video_fx import run_video_fx
from .browser_manager import parse_proxy
from .system_config import load_system_config

GEMINI_MAX_RETRIES = 3
AUTOMATION_MAX_RETRIES = 3


def _can_retry_flow_task(error_message):
    """Không tạo lại khi request cũ có thể vẫn chạy hoặc kết quả đã được tạo."""
    message = (error_message or "").lower()
    no_retry_markers = (
        "không tự retry",
        "không tự gửi lại",
        "không thể tải ảnh",
        "không thể tải video",
        "timeout chờ tải ảnh",
        "timeout chờ tải video",
    )
    return not any(marker in message for marker in no_retry_markers)

class AutomationWorker(QThread):
    progress = pyqtSignal(int, str) # task_id, status
    task_finished = pyqtSignal(int, str) # task_id, result_path
    error = pyqtSignal(int, str) # task_id, error_msg
    retry_requested = pyqtSignal(int, int, int, str)

    def __init__(self, task_id, target="labs.google/fx", config=None, account_id=None):
        super().__init__()
        self.task_id = task_id
        self.target = target
        self.config = config or {}
        self.account_id = account_id
        self._is_paused = False
        self._is_stopped = False

    def pause(self):
        self._is_paused = True

    def resume(self):
        self._is_paused = False

    def stop(self):
        self._is_stopped = True

    def run(self):
        logging.info(f"[Worker {self.task_id}] Bắt đầu thực thi Task...")
        db = SessionLocal()
        task = db.query(Task).filter(Task.id == self.task_id).first()
        if not task:
            logging.error(f"[Worker {self.task_id}] Không tìm thấy task trong DB.")
            self.error.emit(self.task_id, "Không tìm thấy task.")
            db.close()


            return
            
        # Get specified account or fallback to active account
        if self.account_id:
            account = db.query(Account).filter(Account.id == self.account_id).first()
        else:
            account = db.query(Account).filter(Account.is_active == True).order_by(Account.position.asc()).first()
            
        if not account or not account.cookies_json:
            logging.error(f"[Worker {self.task_id}] Không tìm thấy tài khoản hoạt động nào để chạy.")
            self.error.emit(self.task_id, "Không có tài khoản khả dụng hoặc chưa có cookie.")
            db.close()
            return
            
        task.status = "RUNNING"
        task.account_id = account.id
        db.commit()
        
        while self._is_paused and not self._is_stopped:
            QThread.msleep(500)
            
        if self._is_stopped:
            logging.info(f"[Worker {self.task_id}] Nhận tín hiệu dừng task.")
            task.status = "PENDING"
            db.commit()
            db.close()
            self.progress.emit(self.task_id, "Đã dừng")
            return
        
        self.progress.emit(self.task_id, "Đang khởi tạo trình duyệt...")
        
        context = None
        try:
            with sync_playwright() as p:
                from .browser_manager import launch_chrome_and_connect
                chrome_profile = account.chrome_profile or "_tool_profile_"
                proxy_str = account.proxy if account.use_proxy else None
                system_config = load_system_config()
                show_browser = bool(system_config.get("show_chrome_when_running", False))
                
                context = launch_chrome_and_connect(
                    p,
                    account.email,
                    chrome_profile,
                    proxy_str,
                    task_id=self.task_id,
                    show_browser=show_browser
                )
                
                # Nạp cookies từ DB nếu sử dụng profile của tool (mặc định),
                # tránh ghi đè lên cookies hợp lệ đã copy từ profile Chrome gốc của máy.
                if chrome_profile == "_tool_profile_" and account.cookies_json:
                    try:
                        cookies = json.loads(account.cookies_json)
                        context.add_cookies(cookies)
                        logging.info(f"[Worker {self.task_id}] Đã nạp cookies đăng nhập từ DB.")
                    except Exception as cookie_err:
                        logging.warning(f"[Worker {self.task_id}] Không thể nạp cookies từ DB: {cookie_err}")
                
                if "video" in self.target or getattr(task, "task_type", "image") == "video":
                    logging.info(f"[Worker {self.task_id}] Đang chạy kịch bản Video cho prompt: '{task.prompt}'")
                    self.progress.emit(self.task_id, "Đang chạy kịch bản Video...")
                    result = run_video_fx(context, account, task.prompt, self.task_id, self.config)
                elif "gemini" in self.target:
                    logging.info(f"[Worker {self.task_id}] Đang chạy kịch bản Gemini cho prompt: '{task.prompt}'")
                    self.progress.emit(self.task_id, "Đang chạy kịch bản Gemini...")
                    result = run_gemini(context, account, task.prompt, self.task_id, self.config)
                else:
                    logging.info(f"[Worker {self.task_id}] Đang chạy kịch bản ImageFX cho prompt: '{task.prompt}'")
                    self.progress.emit(self.task_id, "Đang chạy kịch bản ImageFX...")
                    result = run_image_fx(context, account, task.prompt, self.task_id, self.config)
                
                # Assuming result is a path to the generated image
                task.status = "COMPLETED"
                task.result_path = result if result else "Lỗi khi lưu kết quả"
                task.retry_count = 0
                db.commit()
                logging.info(f"[Worker {self.task_id}] Hoàn thành task. Kết quả: {task.result_path}")
                
                self.task_finished.emit(self.task_id, task.result_path)
                
        except Exception as e:
            err_msg = str(e)
            logging.error(f"[Worker {self.task_id}] Lỗi trong tiến trình chạy automation: {err_msg}")
            retries_done = task.retry_count or 0
            should_retry = (
                not self._is_stopped
                and retries_done < AUTOMATION_MAX_RETRIES
                and _can_retry_flow_task(err_msg)
            )

            if self._is_stopped or should_retry:
                task.status = "PENDING"
            else:
                task.status = "ERROR"
            
            # Nếu lỗi do tài khoản bị đăng xuất → đánh dấu cookie đã hết hạn trong DB
            if "Tài khoản đã bị đăng xuất" in err_msg or "Signed out" in err_msg:
                from datetime import datetime
                account.cookie_expiry = datetime(2000, 1, 1)  # Đặt về quá khứ = hết hạn
                logging.warning(f"[Worker {self.task_id}] Đã đánh dấu tài khoản ID {account.id} ({account.email}) là Cookie hết hạn.")
            
            db.commit()
            if self._is_stopped:
                self.progress.emit(self.task_id, "PENDING")
            elif should_retry:
                retry_number = retries_done + 1
                task.retry_count = retry_number
                db.commit()
                logging.warning(
                    "[Worker %s] Retry %s/%s; trả task về queue để chạy bằng "
                    "Chrome kế tiếp",
                    self.task_id, retry_number, AUTOMATION_MAX_RETRIES,
                )
                self.retry_requested.emit(
                    self.task_id, account.id, retry_number, err_msg
                )
            else:
                if retries_done >= AUTOMATION_MAX_RETRIES:
                    err_msg = (
                        f"Đã retry {retries_done}/{AUTOMATION_MAX_RETRIES} lần: {err_msg}"
                    )
                self.error.emit(self.task_id, err_msg)
        finally:
            if context:
                try:
                    context.close()
                except Exception as close_err:
                    logging.warning(f"[Worker {self.task_id}] Failed to close Chrome/context: {close_err}")
            db.close()


class GeminiWorker(QThread):
    progress = pyqtSignal(int, str)
    part_progress = pyqtSignal(int, int, int)
    batch_finished = pyqtSignal(int, str)
    error = pyqtSignal(int, str)
    retry_requested = pyqtSignal(int, int, int, str)
    account_unavailable = pyqtSignal(int, int, str)

    def __init__(self, batch_id, account_id, config=None, window_slot=0, window_count=1):
        super().__init__()
        self.batch_id = batch_id
        self.account_id = account_id
        self.config = config or {}
        self.window_slot = window_slot
        self.window_count = window_count
        self._is_paused = False
        self._is_stopped = False

    def pause(self):
        self._is_paused = True

    def resume(self):
        self._is_paused = False

    def stop(self):
        self._is_stopped = True

    def run(self):
        db = SessionLocal()
        context = None
        logging.info(
            "[Gemini Worker %s] Khởi động worker; account_id=%s",
            self.batch_id, self.account_id,
        )
        batch = db.query(GeminiBatch).filter(GeminiBatch.id == self.batch_id).first()
        account = db.query(Account).filter(Account.id == self.account_id).first()
        if not batch or not account:
            logging.error(
                "[Gemini Worker %s] Thiếu dữ liệu: batch_found=%s; account_found=%s",
                self.batch_id, bool(batch), bool(account),
            )
            db.close()
            self.error.emit(self.batch_id, "Không tìm thấy batch hoặc tài khoản")
            return

        batch.status = "RUNNING"
        batch.account_id = account.id
        batch.current_part = 0
        batch.error_message = None
        db.commit()
        logging.info(
            "[Gemini Worker %s] RUNNING; country=%s; account=%s (id=%s); max_gõ_1=%s; marker=%r",
            self.batch_id, batch.country or batch.name, account.email, account.id,
            batch.max_continuations or 10, batch.done_marker or "[[DONE]]",
        )
        self.progress.emit(self.batch_id, "RUNNING")

        try:
            with sync_playwright() as playwright:
                from .browser_manager import launch_chrome_and_connect
                chrome_profile = account.chrome_profile or "_tool_profile_"
                proxy_str = account.proxy if account.use_proxy else None
                show_browser = bool(load_system_config().get("show_chrome_when_running", False))
                logging.info(
                    "[Gemini Worker %s] Mở Chrome; profile=%s; proxy_enabled=%s; "
                    "show_browser=%s; window_slot=%s/%s",
                    self.batch_id, chrome_profile, bool(proxy_str), show_browser,
                    self.window_slot + 1, self.window_count,
                )
                context = launch_chrome_and_connect(
                    playwright, account.email, chrome_profile, proxy_str,
                    task_id=f"gemini_{self.batch_id}",
                    window_slot=(self.window_slot, self.window_count),
                    show_browser=show_browser,
                    preserve_profile_data=True,
                )
                logging.info(
                    "[Gemini Worker %s] Đã kết nối Chrome context; pages=%s",
                    self.batch_id, len(context.pages),
                )
                if chrome_profile == "_tool_profile_" and account.cookies_json:
                    cookies = json.loads(account.cookies_json)
                    context.add_cookies(cookies)
                    logging.info(
                        "[Gemini Worker %s] Đã nạp %s cookie từ DB cho tool profile",
                        self.batch_id, len(cookies),
                    )
                else:
                    logging.info(
                        "[Gemini Worker %s] Dùng session của Chrome Profile; không nạp cookie DB",
                        self.batch_id,
                    )

                worker_config = self.config.copy()
                worker_config.update({
                    "master_prompt": batch.master_prompt,
                    "output_dir": batch.output_dir,
                    "batch_name": LANGUAGE_BY_COUNTRY.get(batch.country, batch.name),
                    "target_country": batch.country or batch.name,
                    "max_continuations": batch.max_continuations or 10,
                    "done_marker": batch.done_marker or "[[DONE]]",
                    "is_paused": lambda: self._is_paused,
                    "is_stopped": lambda: self._is_stopped,
                    "part_callback": self._on_part,
                })
                try:
                    result = run_gemini(
                        context, account, batch.story_content, self.batch_id, worker_config
                    )
                except Exception:
                    # Thu thập khi Playwright vẫn còn hoạt động. Nếu Chrome đã chết,
                    # diagnostics tiến trình bên dưới vẫn đọc được mà không cần CDP.
                    from .browser_manager import log_browser_runtime_diagnostics
                    log_browser_runtime_diagnostics(context, f"Gemini batch {self.batch_id}")
                    try:
                        pages = context.pages
                        error_page = pages[-1] if pages else None
                        diagnostics = {
                            "pages": len(pages),
                            "url": error_page.url if error_page else None,
                            "title": error_page.title() if error_page else None,
                            "model_buttons": error_page.locator('[data-test-id="bard-mode-menu-button"]').count() if error_page else 0,
                            "prompt_boxes": error_page.locator('.ql-editor[contenteditable="true"][role="textbox"]').count() if error_page else 0,
                            "responses": error_page.locator('model-response message-content .markdown').count() if error_page else 0,
                        }
                        logging.error(
                            "[Gemini Worker %s] DOM diagnostics trước khi Playwright đóng: %s",
                            self.batch_id, diagnostics,
                        )
                    except Exception as diagnostic_error:
                        logging.error(
                            "[Gemini Worker %s] DOM đã mất trước khi chụp diagnostics: %s",
                            self.batch_id, diagnostic_error,
                        )
                    raise
                batch.status = "SUCCESS"
                batch.result_path = result
                batch.error_message = None
                batch.retry_count = 0
                db.commit()
                logging.info(
                    "[Gemini Worker %s] SUCCESS; result=%s; account=%s",
                    self.batch_id, result, account.email,
                )
                self.batch_finished.emit(self.batch_id, result)
        except GeminiProUnavailable as exc:
            message = str(exc)
            logging.warning(
                "[Gemini Worker %s] Account id=%s không dùng được model Pro: %s",
                self.batch_id, self.account_id, message,
            )
            batch.status = "PENDING"
            batch.account_id = None
            batch.error_message = message
            db.commit()
            self.account_unavailable.emit(self.batch_id, self.account_id, message)
        except GeminiStopped:
            logging.warning("[Gemini Worker %s] Nhận yêu cầu dừng; trả batch về PENDING", self.batch_id)
            batch.status = "PENDING"
            batch.account_id = None
            db.commit()
            self.progress.emit(self.batch_id, "PENDING")
        except Exception as exc:
            message = str(exc)
            logging.exception(f"[Gemini {self.batch_id}] Automation failed")
            batch.error_message = message
            retries_done = batch.retry_count or 0
            if retries_done < GEMINI_MAX_RETRIES:
                retry_number = retries_done + 1
                batch.retry_count = retry_number
                batch.status = "PENDING"
                db.commit()
                logging.warning(
                    "[Gemini Worker %s] Retry %s/%s; đưa batch về queue để chạy "
                    "bằng Chrome kế tiếp",
                    self.batch_id, retry_number, GEMINI_MAX_RETRIES,
                )
                self.retry_requested.emit(
                    self.batch_id, self.account_id, retry_number, message
                )
            else:
                batch.status = "FAILED"
                db.commit()
                logging.error(
                    "[Gemini Worker %s] FAILED sau %s lần retry",
                    self.batch_id, retries_done,
                )
                self.error.emit(
                    self.batch_id,
                    f"Đã retry {retries_done}/{GEMINI_MAX_RETRIES} lần: {message}",
                )
        finally:
            if context:
                try:
                    logging.info("[Gemini Worker %s] Đóng Chrome context", self.batch_id)
                    context.close()
                except Exception as close_error:
                    logging.warning(
                        "[Gemini Worker %s] Lỗi khi đóng context: %s",
                        self.batch_id, close_error,
                    )
            db.close()
            logging.info("[Gemini Worker %s] Worker kết thúc", self.batch_id)

    def _on_part(self, current, total):
        part_db = SessionLocal()
        try:
            batch = part_db.query(GeminiBatch).filter(GeminiBatch.id == self.batch_id).first()
            if batch:
                batch.current_part = current
                part_db.commit()
                logging.info(
                    "[Gemini Worker %s] Tiến độ gõ '1': %s/%s",
                    self.batch_id, current, total,
                )
        finally:
            part_db.close()
        self.part_progress.emit(self.batch_id, current, total)
