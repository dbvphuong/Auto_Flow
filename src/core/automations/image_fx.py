import os
import time
import logging
import re
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from .flow_ui import (
    click_generate, configure_generation, dismiss_dashboard_promos,
    enter_flow_app, find_prompt_input, generation_is_busy, open_new_project,
)


class FlowGenerationFailed(Exception):
    pass


def _clean_error_files(save_path, final_name):
    for suffix in ["fail.png", "fail.html", "err.png", "err.html"]:
        old_err_file = os.path.join(save_path, f"error_{final_name}_{suffix}")
        try:
            if os.path.exists(old_err_file):
                os.remove(old_err_file)
        except:
            pass


def _quality_file_path(save_path, final_name, quality):
    suffix = "" if quality == "1K" else f"_{quality}"
    return os.path.join(save_path, f"{final_name}{suffix}.jpg")


def _upscale_error_visible(page):
    try:
        error_icon_toast = page.locator('[data-sonner-toast=""]:has(i:has-text("error")), li:has(i:has-text("error"))').first
        if error_icon_toast.is_visible():
            return True
    except Exception:
        pass

    error_texts = [
        "Không tăng độ phân giải được",
        "Unable to upscale",
        "Can't upscale",
        "Cannot upscale",
    ]
    for text in error_texts:
        try:
            toast = page.locator(f'[data-sonner-toast=""]:has-text("{text}"), li:has-text("{text}")').first
            if toast.is_visible():
                return True
        except Exception:
            pass
    return False


def _close_welcome_popups(page):
    close_keywords = [
        "Đóng", "Close", "Đóng cửa sổ phụ này", 
        "Got it", "Đã hiểu", "close Đóng", "Bỏ qua", "Skip",
        "Tôi đồng ý", "Agree"
    ]
    # Chỉ đóng nút nằm trong modal thực sự. Selector toàn trang trước đây có thể
    # bấm nhầm nút Close của workspace/panel và làm biến mất ô nhập prompt.
    dialogs = page.locator(
        '[role="dialog"], [aria-modal="true"], '
        '[data-radix-dialog-content], [data-state="open"][data-radix-portal]'
    )
    for dialog_index in range(dialogs.count()):
        dialog = dialogs.nth(dialog_index)
        try:
            if not dialog.is_visible():
                continue
            for keyword in close_keywords:
                buttons = dialog.get_by_role(
                    "button", name=re.compile(rf"^\s*{re.escape(keyword)}\s*$", re.I)
                )
                for button_index in range(buttons.count()):
                    button = buttons.nth(button_index)
                    if button.is_visible():
                        logging.info(f"[Flow] Tự động đóng popup: {keyword}")
                        button.click(force=True)
                        page.wait_for_timeout(500)
                        break
        except Exception:
            continue
    dismiss_dashboard_promos(page)

def _close_visible_toasts(page):
    for text in ["Đóng", "Close"]:
        try:
            btn = page.locator(f'[data-sonner-toast=""] button:has-text("{text}"), li button:has-text("{text}")').first
            if btn.is_visible():
                btn.click(force=True)
                page.wait_for_timeout(300)
                return
        except Exception:
            pass


def _open_download_quality_menu(page, generated_img):
    generated_img.hover()
    page.wait_for_timeout(1000)

    more_btn = page.locator('button:has(i:has-text("more_vert"))').last
    more_btn.wait_for(state="visible", timeout=5000)
    more_btn.click(force=True)
    page.wait_for_timeout(1500)

    download_menu = page.locator('[role="menuitem"]:has-text("Tải xuống"), [role="menuitem"]:has-text("Download")').first
    download_menu.wait_for(state="visible", timeout=5000)
    download_menu.hover()
    page.wait_for_timeout(1500)


def _download_quality(page, generated_img, quality, file_path):
    _open_download_quality_menu(page, generated_img)
    logging.debug(f"[Flow] Chọn chất lượng tải xuống: {quality}")

    quality_btn = page.locator(f'[role="menuitem"]:has-text("{quality}"), button:has-text("{quality}")').first
    quality_btn.wait_for(state="visible", timeout=5000)

    downloads = []
    page.on("download", lambda download: downloads.append(download))
    quality_btn.click(force=True)

    deadline = time.time() + 90
    while time.time() < deadline:
        if downloads:
            downloads[0].save_as(file_path)
            return True
        if quality != "1K" and _upscale_error_visible(page):
            _close_visible_toasts(page)
            return False
        page.wait_for_timeout(500)

    raise PlaywrightTimeoutError(f"Timeout chờ tải ảnh {quality}")


def _download_quality_with_retry(page, generated_img, quality, file_path, attempts=3):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return _download_quality(page, generated_img, quality, file_path)
        except Exception as exc:
            last_error = exc
            if "Target page, context or browser has been closed" in str(exc):
                raise
            logging.warning(
                "[Flow] Tải ảnh %s lỗi lần %s/%s: %s",
                quality, attempt, attempts, exc,
            )
            if attempt < attempts:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                page.wait_for_timeout(1500)
    raise last_error


def _generation_failed_visible(page):
    return _generation_failed_count(page) > 0


def _generation_failed_count(page):
    return len(_generation_failed_tile_ids(page, visible_only=True))


def _visible_agent_error(page):
    """Detect new-UI agent failures that are rendered outside data-tile-id."""
    try:
        return page.locator("body").evaluate(
            """(body) => {
                const errorPattern = /Không thành công|Tác nhân đang bị quá tải|vui lòng thử lại sau vài phút|Unsuccessful|Agent is overloaded|try again in a few minutes/i;
                const isReallyVisible = (el) => {
                    if (!el || !(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) return false;
                    let current = el;
                    while (current && current !== document.body) {
                        const style = getComputedStyle(current);
                        const opacity = Number.parseFloat(style.opacity);
                        if (style.display === 'none' || style.visibility === 'hidden'
                                || (!Number.isNaN(opacity) && opacity < 0.1)) return false;
                        current = current.parentElement;
                    }
                    return true;
                };
                return Array.from(body.querySelectorAll('div, span, p')).some(el => {
                    const ownText = Array.from(el.childNodes)
                        .filter(node => node.nodeType === Node.TEXT_NODE)
                        .map(node => node.textContent || '').join(' ').trim();
                    return ownText && errorPattern.test(ownText) && isReallyVisible(el);
                });
            }"""
        )
    except Exception:
        return False


def _tile_generation_progress(page, tile_id):
    """Return a visible percentage for a tile, ignoring its hidden failure card."""
    if not tile_id:
        return None
    try:
        return page.evaluate(
            r"""(tileId) => {
                const isReallyVisible = (el) => {
                    if (!el || !(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) return false;
                    let current = el;
                    while (current && current !== document.body) {
                        const style = getComputedStyle(current);
                        const opacity = Number.parseFloat(style.opacity);
                        if (style.display === 'none' || style.visibility === 'hidden'
                                || (!Number.isNaN(opacity) && opacity < 0.1)) return false;
                        current = current.parentElement;
                    }
                    return true;
                };
                const tiles = Array.from(document.querySelectorAll('[data-tile-id]'))
                    .filter(tile => tile.getAttribute('data-tile-id') === tileId && isReallyVisible(tile));
                for (const tile of tiles) {
                    for (const el of tile.querySelectorAll('div, span, p, a')) {
                        if (!isReallyVisible(el)) continue;
                        const ownText = Array.from(el.childNodes)
                            .filter(node => node.nodeType === Node.TEXT_NODE)
                            .map(node => node.textContent || '').join(' ').trim();
                        const match = ownText.match(/^([0-9]{1,3})\s*%$/);
                        if (match) return Number(match[1]);
                    }
                }
                return null;
            }""",
            tile_id,
        )
    except Exception:
        return None


def _page_generation_progress(page):
    """Return any genuinely visible generation percentage, including pre-tile UI."""
    try:
        return page.locator("body").evaluate(
            r"""(body) => {
                const isReallyVisible = (el) => {
                    if (!el || !(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) return false;
                    let current = el;
                    while (current && current !== document.body) {
                        const style = getComputedStyle(current);
                        const opacity = Number.parseFloat(style.opacity);
                        if (style.display === 'none' || style.visibility === 'hidden'
                                || (!Number.isNaN(opacity) && opacity < 0.1)) return false;
                        current = current.parentElement;
                    }
                    return true;
                };
                for (const el of body.querySelectorAll('div, span, p, a')) {
                    if (!isReallyVisible(el)) continue;
                    const ownText = Array.from(el.childNodes)
                        .filter(node => node.nodeType === Node.TEXT_NODE)
                        .map(node => node.textContent || '').join(' ').trim();
                    const match = ownText.match(/^([0-9]{1,3})\s*%$/);
                    if (match) return Number(match[1]);
                }
                return null;
            }"""
        )
    except Exception:
        return None


def _active_prompt_texts(page):
    """Read current prompt editors without waiting on a stale Playwright locator."""
    try:
        return page.evaluate(
            """() => Array.from(document.querySelectorAll(
                    '[role="textbox"][contenteditable="true"], textarea:not([name*="recaptcha"])'))
                .filter(el => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0
                        && style.display !== 'none' && style.visibility !== 'hidden';
                })
                .map(el => (el.value !== undefined ? el.value : el.textContent || '').trim())"""
        )
    except Exception:
        return []


def get_error_keywords():
    import json
    import os
    src_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    json_path = os.path.join(src_dir, "data", "error_keywords.json")
    
    default_keywords = [
        "Không thành công",
        "Không thể tạo",
        "Đã xảy ra lỗi",
        "hoạt động bất thường",
        "bất thường",
        "Vui lòng thử lại",
        "Unsuccessful",
        "Failed",
        "Something went wrong",
        "unusual activity"
    ]
    
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("error_keywords", default_keywords)
        except Exception:
            pass
    return default_keywords

def _generation_failed_tile_ids(page, visible_only=True):
    keywords = get_error_keywords()
    try:
        return page.locator("[data-tile-id]").evaluate_all(
            """(tiles, args) => {
                const visibleOnly = args.visibleOnly;
                const keywords = args.keywords;
                const isRealVisible = (el) => {
                    if (!el) return false;
                    if (!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) return false;
                    
                    let parent = el;
                    while (parent) {
                        if (parent === document.body) break;
                        const style = window.getComputedStyle(parent);
                        const opacity = Number.parseFloat(style.opacity);
                        if (style.display === 'none' || style.visibility === 'hidden' || (!Number.isNaN(opacity) && opacity < 0.1)) {
                            return false;
                        }
                        parent = parent.parentElement;
                    }
                    return true;
                };

                return tiles.filter(tile => {
                    const visible = isRealVisible(tile);
                    if (visibleOnly && !visible) return false;

                    const visibleWarningIcons = Array.from(tile.querySelectorAll("i")).filter(icon => {
                        const iconName = (icon.textContent || "").trim().toLowerCase();
                        const iconVisible = isRealVisible(icon);
                        return (!visibleOnly || iconVisible) && (iconName === "warning" || iconName === "warning_amber" || iconName === "error");
                    });

                    const hasVisibleFailureCard = visibleWarningIcons.some(icon => {
                        let card = icon.parentElement;
                        while (card && card !== tile.parentElement) {
                            if ((!visibleOnly || isRealVisible(card))) {
                                const ownText = (card.textContent || "").toLowerCase();
                                if (keywords.some(kw => ownText.includes(kw.toLowerCase()))) return true;
                            }
                            if (card === tile) break;
                            card = card.parentElement;
                        }
                        return false;
                    });

                    return hasVisibleFailureCard;
                }).map(tile => tile.getAttribute("data-tile-id") || "");
            }""",
            { "visibleOnly": visible_only, "keywords": keywords }
        )
    except Exception as e:
        logging.error(f"[Flow] Lỗi kiểm tra tile lỗi: {e}")
        return []


def _generated_image_count(page):
    try:
        imgs = page.locator('img[alt="Generated image"], img[alt="Hình ảnh được tạo"]')
        visible_count = 0
        for idx in range(imgs.count()):
            try:
                if imgs.nth(idx).is_visible():
                    visible_count += 1
            except Exception:
                pass
        return visible_count
    except Exception:
        return 0


def run_image_fx(context, account, prompt, task_id, config):
    # Retry task được điều phối ở queue để lần sau dùng Chrome/account khác.
    # Các retry tải file riêng vẫn ở cùng trang vì không tạo thêm ảnh mới.
    max_attempts = 1
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt > 1:
                logging.info(f"[Flow] Thử lại tạo ảnh lần {attempt}/{max_attempts}...")
            return _run_image_fx_once(context, account, prompt, task_id, config)
        except Exception as exc:
            last_error = exc
            err_msg = str(exc)
            if ("Tài khoản đã bị đăng xuất" in err_msg or "Signed out" in err_msg
                    or "Target page, context or browser has been closed" in err_msg
                    or "Không thể tải ảnh" in err_msg
                    or "Flow vẫn đang tạo ảnh" in err_msg):
                logging.error(f"[Flow] Bỏ qua retry với lỗi không nên tạo lại: {err_msg}")
                raise
            
            logging.warning(f"[Flow] Thử lại tạo ảnh thất bại lần {attempt}/{max_attempts}: {err_msg}")
            if attempt >= max_attempts:
                break
                
            # Flow thường chỉ quá tải trong chốc lát; giãn lần thử để request sau
            # không đập ngay vào cùng một lỗi máy chủ.
            time.sleep(5)
    raise Exception(f"Flow tạo ảnh Không thành công sau {max_attempts} lần thử: {last_error}")


def _run_image_fx_once(context, account, prompt, task_id, config):
    """
    Kịch bản tự động hóa trên Google Labs Flow (https://labs.google/fx/vi/tools/flow)
    Sử dụng cookie đã lưu từ account để đăng nhập.
    """
    save_path = config.get("save_path", "")
    if not save_path or not save_path.strip():
        save_path = "output"
        
    final_name = config.get("final_name", str(task_id))
    
    if not os.path.exists(save_path):
        os.makedirs(save_path, exist_ok=True)
    _clean_error_files(save_path, final_name)
        
    page = context.new_page()
    try:
        logging.info(f"[Flow] Điều hướng tới Google Labs Flow...")
        # Sử dụng phiên bản tiếng Việt để khớp chính xác record
        page.goto("https://labs.google/fx/vi/tools/flow", timeout=60000)
        page.wait_for_timeout(3000)
        
        # 1. Tự động đóng các popup quảng cáo/chào mừng
        _close_welcome_popups(page)

        # URL /fx/tools/flow đôi khi mở trang giới thiệu công khai trước. Đây
        # không phải trạng thái đăng xuất; phải vào ứng dụng bằng CTA chính.
        enter_flow_app(page)

        # 2. Kiểm tra trạng thái đăng nhập và Xử lý nút Dự án mới (New project) hoặc Bắt đầu
        # Đợi các chuyển hướng SetSID (nếu có) hoàn tất để tránh nhận diện nhầm URL đăng xuất
        for _ in range(30):
            if "SetSID" not in page.url and "accounts.google" not in page.url:
                break
            page.wait_for_timeout(500)
            
        # Tự động xử lý trang Lựa chọn tài khoản (Account Chooser) nếu có
        if "accountchooser" in page.url or "AccountChooser" in page.url:
            logging.info(f"[Flow] Phát hiện trang Lựa chọn tài khoản (Account Chooser). Đang tự động chọn tài khoản: {account.email}...")
            try:
                email_card = page.locator(f'text="{account.email}"').first
                if not email_card.is_visible():
                    email_card = page.locator(f'div:has-text("{account.email}"), span:has-text("{account.email}"), p:has-text("{account.email}"), li:has-text("{account.email}")').last
                
                if email_card.is_visible():
                    email_card.click(force=True)
                    logging.info("[Flow] Đã chọn tài khoản, đợi chuyển hướng...")
                    page.wait_for_timeout(5000)
                    
                    # Đợi chuyển hướng SetSID hoàn tất sau khi click chọn tài khoản
                    for _ in range(30):
                        if "SetSID" not in page.url and "accounts.google" not in page.url:
                            break
                        page.wait_for_timeout(500)
                else:
                    logging.warning(f"[Flow] Không thấy card tài khoản cho email: {account.email}")
            except Exception as select_err:
                logging.warning(f"[Flow] Lỗi khi tự động chọn tài khoản: {select_err}")
            
        login_btn = page.locator('button:has-text("Đăng nhập"), button:has-text("Sign in"), a:has-text("Sign in"), a:has-text("Đăng nhập"), [aria-label*="Sign in" i]').first
        is_signed_out = False
        if login_btn.is_visible():
            is_signed_out = True
        elif "accounts.google" in page.url and ("signin" in page.url or "ServiceLogin" in page.url):
            is_signed_out = True
            
        if is_signed_out:
            raise Exception("Tài khoản đã bị đăng xuất (Signed out) trên Google. Vui lòng đăng nhập lại tài khoản này trên giao diện Tool.")

        try:
            logging.info("[Flow] Đang xác định trạng thái trang...")
            dashboard_element = page.locator(
                'button:has-text("Dự án mới"), button:has-text("New project")'
            ).first
            work_node_element = page.locator('div:has-text("Bắt đầu tạo hoặc thả nội dung nghe nhìn"), div:has-text("Start creating or drop media"), div:has-text("Bắt đầu tạo")').last
            workspace_prompt = page.locator(
                '[role="textbox"][contenteditable="true"], textarea:not([name*="recaptcha"])'
            )
            
            found_where = None
            for _ in range(40): # Tối đa 20 giây
                if dashboard_element.is_visible():
                    found_where = "dashboard"
                    break
                if (work_node_element.is_visible()
                        or workspace_prompt.count() > 0 and workspace_prompt.last.is_visible()):
                    found_where = "workspace"
                    break
                page.wait_for_timeout(500)
                
            if found_where == "dashboard":
                logging.info("[Flow] Phát hiện đang ở trang chủ/dashboard. Khởi tạo Dự án mới...")
                
                _close_welcome_popups(page)
                open_new_project(page, attempts=3)

                if "accounts.google" in page.url and ("signin" in page.url or "ServiceLogin" in page.url):
                    raise Exception("Tài khoản đã bị đăng xuất (Signed out) trên Google. Vui lòng đăng nhập lại tài khoản này trên giao diện Tool.")

                # Đợi trang workspace mới tải xong (chờ thanh prompt hoặc work node xuất hiện)
                workspace_indicator = page.locator(
                    '[role="textbox"][contenteditable="true"], textarea:not([name*="recaptcha"]), '
                    'p:has-text("Bạn muốn tạo gì?"), p:has-text("What do you want to create?"), '
                    'div:has-text("Bắt đầu tạo hoặc thả nội dung nghe nhìn"), div:has-text("Start creating or drop media"), '
                    'div:has-text("Bắt đầu tạo")'
                ).first
                try:
                    workspace_indicator.wait_for(state="visible", timeout=15000)
                    logging.info("[Flow] Trang workspace đã tải xong.")
                except Exception:
                    logging.warning("[Flow] Timeout chờ trang workspace tải xong, thử tiếp tục...")
                
                try:
                    start_btn = page.locator('button:has-text("Bắt đầu"), button:has-text("Get started"), button:has-text("Bắt đầu tạo")').first
                    if start_btn.is_visible():
                        logging.info("[Flow] Click nút Bắt đầu...")
                        start_btn.click(force=True)
                        page.wait_for_timeout(2000)
                except Exception:
                    pass
            elif found_where == "workspace":
                logging.debug("[Flow] Đã ở sẵn trong workspace/canvas.")
            else:
                logging.warning("[Flow] Không tự động xác định được page state. Thử click Dự án mới nếu có...")
                if dashboard_element.is_visible():
                    dashboard_element.click(force=True)
                    workspace_indicator = page.locator(
                        'p:has-text("Bạn muốn tạo gì?"), p:has-text("What do you want to create?"), '
                        'div:has-text("Bắt đầu tạo hoặc thả nội dung nghe nhìn"), div:has-text("Start creating or drop media"), '
                        'div:has-text("Bắt đầu tạo")'
                    ).first
                    try:
                        workspace_indicator.wait_for(state="visible", timeout=25000)
                    except:
                        pass
        except Exception as e:
            if ("Tài khoản đã bị đăng xuất" in str(e)
                    or "Flow không mở được dự án" in str(e)):
                raise
            logging.debug(f"[Flow] Bỏ qua bước xác định trang: {e}")

        # Đóng lại các popup quảng cáo nếu xuất hiện sau khi tạo dự án
        _close_welcome_popups(page)

        # 3. Click vào hộp Canvas Node (Vùng làm việc) để kích hoạt thanh prompt dưới chân trang
        try:
            # Kiểm tra xem ô soạn thảo prompt đã xuất hiện hay chưa để tránh click work node vô ích
            prompt_input_preview = page.locator('textarea:not([name*="recaptcha"]), [contenteditable="true"]').last
            prompt_trigger_preview = page.locator('p:has-text("Bạn muốn tạo gì?"), p:has-text("What do you want to create?")').first
            
            # Đợi tối đa 8 giây cho prompt_input_preview hoặc prompt_trigger_preview xuất hiện trước khi quyết định click work node
            for _ in range(16):
                if prompt_input_preview.is_visible() or prompt_trigger_preview.is_visible():
                    break
                page.wait_for_timeout(500)
            
            if not (prompt_input_preview.is_visible() or prompt_trigger_preview.is_visible()):
                logging.debug("[Flow] Kích hoạt vùng làm việc (work node)...")
                work_node = page.locator('div:has-text("Bắt đầu tạo hoặc thả nội dung nghe nhìn"), div:has-text("Start creating or drop media"), div:has-text("Bắt đầu tạo")').last
                work_node.wait_for(state="visible", timeout=5000)
                work_node.click(force=True)
                page.wait_for_timeout(500)
                work_node.click(force=True)
                page.wait_for_timeout(500)
            else:
                logging.debug("[Flow] Ô soạn thảo prompt đã hiển thị sẵn, bỏ qua bước kích hoạt work node.")
        except Exception as e:
            logging.warning(f"[Flow] Bỏ qua hoặc lỗi khi click node làm việc: {e}. Thử tiếp tục...")

        # 4. Tập trung vào ô prompt và tắt Tác nhân (Agent) nếu đang bật
        try:
            # Tìm ô prompt hoạt động thực sự (lọc các phần tử hiển thị)
            prompt_input = find_prompt_input(page)
            prompt_input.click(force=True)
            page.wait_for_timeout(500)
            
            # Kiểm tra trạng thái Agent qua nút Agent hiển thị thực tế và thuộc tính aria-pressed
            agent_btn = page.locator('button:has-text("Tác nhân"), button:has-text("Agent")').first
            try:
                agent_btn.wait_for(state="attached", timeout=3000)
            except:
                pass
                
            new_settings_btn = page.locator('button:has-text("tune")').first
            if not new_settings_btn.is_visible() and agent_btn.count() > 0:
                is_pressed = agent_btn.get_attribute("aria-pressed")
                # Nếu nút đang được nhấn (aria-pressed="true") -> Agent đang active, cần click để tắt
                if is_pressed == "true":
                    logging.info("[Flow] Phát hiện chế độ Tác nhân (Agent) đang bật. Đang click để tắt...")
                    agent_btn.click(force=True)
                    page.wait_for_timeout(1500)
                    # Click lại prompt input để kích hoạt hiển thị nút cấu hình sau khi tắt Agent
                    prompt_input.click(force=True)
                    page.wait_for_timeout(1000)
                
            # Điền prompt
            logging.info(f"[Flow] Điền prompt: '{prompt}'")
            prompt_input.fill(prompt)
            page.wait_for_timeout(1000)
        except Exception as e:
            raise RuntimeError(f"Không thể điền prompt ảnh vào Google Flow: {e}") from e

        # 5. Cấu hình theo đúng giao diện Flow đang hiển thị.
        layout = configure_generation(page, prompt_input, "image", config)

        # 6. Click nút Tạo (Tạo / Generate / Create) - nút có icon arrow_forward
        try:
            logging.info("[Flow] Nhấn nút Tạo...")
            failure_baseline = set(_generation_failed_tile_ids(page, visible_only=False))
            image_baseline = _generated_image_count(page)
            tile_baseline = set(page.locator('[data-tile-id]').evaluate_all(
                'elements => elements.map(el => el.getAttribute("data-tile-id"))'
            ))
            new_tile_id = None

            # The agent UI occasionally ignores the first click while retaining
            # the prompt. Click again only when there is positive evidence that
            # nothing was submitted: prompt still present and no new tile/image.
            submitted = False
            for send_attempt in range(1, 4):
                click_generate(page, prompt_input)
                ack_deadline = time.time() + 12
                while time.time() < ack_deadline:
                    current_tiles = page.locator('[data-tile-id]').evaluate_all(
                        'elements => elements.map(el => el.getAttribute("data-tile-id"))'
                    )
                    new_tiles = [tile_id for tile_id in current_tiles if tile_id not in tile_baseline]
                    if new_tiles:
                        new_tile_id = new_tiles[-1]
                        submitted = True
                        break
                    if _generated_image_count(page) > image_baseline:
                        submitted = True
                        break
                    if _page_generation_progress(page) is not None:
                        submitted = True
                        break
                    if generation_is_busy(page):
                        logging.info(
                            "[Flow] Web đã nhận prompt và đang diễn giải; chuyển sang vòng chờ tạo ảnh."
                        )
                        submitted = True
                        break
                    prompt_texts = _active_prompt_texts(page)
                    if not prompt_texts or all(not text for text in prompt_texts):
                        submitted = True
                        break
                    page.wait_for_timeout(500)
                if submitted:
                    break
                logging.warning(
                    "[Flow] Web chưa nhận lần bấm Tạo %s/3; prompt vẫn còn, bấm lại an toàn.",
                    send_attempt,
                )
            if not submitted:
                raise FlowGenerationFailed(
                    "Flow không nhận prompt sau 3 lần bấm Tạo; chưa phát sinh tile."
                )

            # One overall ten-minute deadline starts after the request is accepted.
            # The agent UI can spend minutes interpreting before creating a tile/%.
            generation_deadline = time.time() + 600

            while new_tile_id is None and time.time() < generation_deadline:
                current_tiles = page.locator('[data-tile-id]').evaluate_all(
                    'elements => elements.map(el => el.getAttribute("data-tile-id"))'
                )
                new_tiles = [tile_id for tile_id in current_tiles if tile_id not in tile_baseline]
                if new_tiles:
                    new_tile_id = new_tiles[-1]
                    break
                if _generated_image_count(page) > image_baseline:
                    break
                if (_page_generation_progress(page) is None and (
                        _visible_agent_error(page)
                        or set(_generation_failed_tile_ids(page, visible_only=True)) - failure_baseline)):
                    raise FlowGenerationFailed("Flow bao loi: Khong thanh cong")
                page.wait_for_timeout(1000)
            if (new_tile_id is None and _generated_image_count(page) <= image_baseline
                    and _page_generation_progress(page) is None):
                if generation_is_busy(page):
                    raise RuntimeError(
                        "Flow vẫn đang diễn giải/tạo ảnh sau 10 phút; không tự gửi lại để tránh tạo ảnh trùng."
                    )
                raise FlowGenerationFailed("Flow khong tao tile/anh moi sau khi gui prompt.")
        except FlowGenerationFailed:
            raise
        except Exception as e:
            raise FlowGenerationFailed(f"Không thể gửi prompt tạo ảnh: {e}") from e

        # 7. Đợi quá trình sinh ảnh kết thúc
        logging.info("[Flow] Đang đợi ảnh được tạo hoàn thành...")
        # Ảnh tạo xong sẽ có thẻ <img alt="Hình ảnh được tạo"> hoặc <img alt="Generated image">
        result_scope = page.locator(f'[data-tile-id="{new_tile_id}"]').last if new_tile_id else page
        generated_img = result_scope.locator(
            'img[alt="Hình ảnh được tạo"], img[alt="Generated image"]'
        ).last
        last_progress = None
        while time.time() < generation_deadline:
            try:
                if generated_img.is_visible():
                    logging.info("[Flow] Ảnh đã được tạo xong!")
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                pass

            progress = (_tile_generation_progress(page, new_tile_id)
                        if new_tile_id else _page_generation_progress(page))
            if progress is not None:
                if progress != last_progress:
                    logging.info("[Flow] Ảnh đang được tạo: %s%%; tiếp tục chờ...", progress)
                    last_progress = progress
            else:
                failed = (_visible_agent_error(page)
                          or new_tile_id in _generation_failed_tile_ids(page, visible_only=True))
                if failed:
                    raise FlowGenerationFailed("Flow báo lỗi tạo ảnh thật sự sau khi tile ngừng chạy.")
            page.wait_for_timeout(2000)
        else:
            remaining_progress = (_tile_generation_progress(page, new_tile_id)
                                  if new_tile_id else _page_generation_progress(page))
            if remaining_progress is not None:
                raise RuntimeError(
                    "Flow vẫn đang tạo ảnh sau 10 phút; không tự retry để tránh tạo ảnh trùng."
                )
            raise FlowGenerationFailed("Flow không hoàn tất ảnh sau 10 phút.")
        
        # 8. Tải ảnh - Flow: Hover ảnh → Click ⋮ → Hover "Tải xuống" → Chọn chất lượng
        current_progress = (_tile_generation_progress(page, new_tile_id)
                            if new_tile_id else _page_generation_progress(page))
        if (current_progress is None and (
                _visible_agent_error(page)
                or set(_generation_failed_tile_ids(page, visible_only=True)) - failure_baseline)):
            raise FlowGenerationFailed("Flow bao loi: Khong thanh cong")

        qualities = config.get("quality", ["1K"])
        target_quality = "1K"
        if "4K" in qualities:
            target_quality = "4K"
        elif "2K" in qualities:
            target_quality = "2K"
            
        file_path = _quality_file_path(save_path, final_name, target_quality)
        
        try:
            generated_img = result_scope.locator(
                'img[alt="Hình ảnh được tạo"], img[alt="Generated image"]'
            ).last
            logging.debug("[Flow] Hover vao anh da tao de hien menu tai xuong...")
            downloaded = _download_quality_with_retry(
                page, generated_img, target_quality, file_path, attempts=3
            )
            if not downloaded and target_quality != "1K":
                fallback_quality = "1K"
                fallback_path = _quality_file_path(save_path, final_name, fallback_quality)
                logging.warning(
                    f"[Flow] Khong the tai {target_quality}/upscale. Tu dong chuyen ver {fallback_quality}: {fallback_path}"
                )
                _download_quality_with_retry(
                    page, generated_img, fallback_quality, fallback_path, attempts=3
                )
                target_quality = fallback_quality
                file_path = fallback_path
            
            
            logging.info(f"[Flow] Đã lưu ảnh {target_quality} thành công vào: {file_path}")
            _clean_error_files(save_path, final_name)
            try:
                from core.browser_manager import update_account_credits_and_type_from_page
                update_account_credits_and_type_from_page(page, account.id)
            except Exception as cu_err:
                logging.warning(f"[Flow] Không thể cập nhật credits: {cu_err}")
            page.close()
            return file_path
            
        except Exception as e:
            logging.warning(f"[Flow] Lỗi khi tải ảnh qua menu: {e}. Thử fallback lấy src img...")
        
        # 8e. Fallback: Tải ảnh trực tiếp qua thuộc tính src của thẻ <img>
        try:
            generated_img = result_scope.locator(
                'img[alt="Hình ảnh được tạo"], img[alt="Generated image"]'
            ).last
            img_src = generated_img.get_attribute("src")
            if img_src:
                if target_quality != "1K" and _upscale_error_visible(page):
                    _close_visible_toasts(page)
                    target_quality = "1K"
                    file_path = _quality_file_path(save_path, final_name, target_quality)
                    logging.debug(f"[Flow] Upscale lỗi, lưu fallback 1K vào: {file_path}")
                if img_src.startswith("data:image"):
                    import base64
                    header, encoded = img_src.split(",", 1)
                    data = base64.b64decode(encoded)
                    with open(file_path, "wb") as f:
                        f.write(data)
                else:
                    import requests
                    pointer = requests.get(img_src)
                    with open(file_path, "wb") as f:
                        f.write(pointer.content)
                logging.info(f"[Flow] Đã lưu ảnh qua src img thành công: {file_path}")
                _clean_error_files(save_path, final_name)
                try:
                    from core.browser_manager import update_account_credits_and_type_from_page
                    update_account_credits_and_type_from_page(page, account.id)
                except Exception as cu_err:
                    logging.warning(f"[Flow] Không thể cập nhật credits: {cu_err}")
                page.close()
                return file_path
            else:
                raise Exception("Không tìm thấy thuộc tính src trên thẻ img kết quả.")
        except Exception as err:
            try:
                debug_path = "debug_flow_error.html"
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(page.content())
                # Tự động xóa file debug sau khi log
                if os.path.exists(debug_path):
                    os.remove(debug_path)
            except:
                pass
            raise Exception(f"Không thể tải ảnh: {err}")

    except FlowGenerationFailed:
        try:
            if "accounts.google.com" in page.url or "signin" in page.url:
                page.close()
                raise Exception("Tài khoản đã bị đăng xuất (Signed out) trên Google. Vui lòng đăng nhập lại tài khoản này trên giao diện Tool.")
            err_img_path = os.path.join(save_path, f"error_{final_name}_fail.png")
            err_html_path = os.path.join(save_path, f"error_{final_name}_fail.html")
            page.screenshot(path=err_img_path)
            logging.warning(f"[Flow] Đã chụp ảnh màn hình lỗi tạo ảnh tại: {err_img_path}")
            with open(err_html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            # Giữ lại file error để người dùng kiểm tra lỗi
        except Exception as se:
            if "Tài khoản đã bị đăng xuất" in str(se):
                raise
            logging.warning(f"[Flow] Không thể chụp ảnh màn hình lỗi: {se}")
        page.close()
        raise
    except Exception as e:
        try:
            if "accounts.google.com" in page.url or "signin" in page.url:
                page.close()
                raise Exception("Tài khoản đã bị đăng xuất (Signed out) trên Google. Vui lòng đăng nhập lại tài khoản này trên giao diện Tool.")
            err_img_path = os.path.join(save_path, f"error_{final_name}_err.png")
            err_html_path = os.path.join(save_path, f"error_{final_name}_err.html")
            page.screenshot(path=err_img_path)
            logging.warning(f"[Flow] Đã chụp ảnh màn hình lỗi hệ thống tại: {err_img_path}")
            with open(err_html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            # Giữ lại file error để người dùng kiểm tra lỗi
        except Exception as se:
            if "Tài khoản đã bị đăng xuất" in str(se):
                raise
            logging.warning(f"[Flow] Không thể chụp ảnh màn hình lỗi: {se}")
        page.close()
        raise Exception(f"Lỗi kịch bản Flow: {str(e)}")
