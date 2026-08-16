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
    # Dạng tên: {final_name}_{quality}.mp4
    return os.path.join(save_path, f"{final_name}_{quality}.mp4")

def _close_welcome_popups(page):
    close_keywords = [
        "Đóng", "Close", "Đóng cửa sổ phụ này", 
        "Got it", "Đã hiểu", "close Đóng", "Bỏ qua", "Skip",
        "Tôi đồng ý", "Agree"
    ]
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
                        logging.info(f"[Flow Video] Tự động đóng popup: {keyword}")
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

def _open_download_menu(page, generated_tile):
    # 1. Hover vào tile container trước để kích hoạt hiển thị nút 3 chấm
    try:
        generated_tile.hover()
        page.wait_for_timeout(1000)
    except Exception as e:
        logging.warning(f"[Flow Video] Lỗi hover tile: {e}")

    # 2. Tìm nút menu 3 chấm của riêng tile này
    more_btn = generated_tile.locator('button:has(i:has-text("more_vert")), button:has-text("more_vert"), [aria-label*="more" i]').first
    
    # Chỉ cần đợi nút more_btn được đính kèm (attached) vào DOM
    try:
        more_btn.wait_for(state="attached", timeout=8000)
    except Exception:
        logging.warning("[Flow Video] Không tìm thấy nút 3 chấm trong tile, thử tìm kiếm toàn cục...")
        more_btn = page.locator('button:has(i:has-text("more_vert")), button:has-text("more_vert"), [aria-label*="more" i]').last
        more_btn.wait_for(state="attached", timeout=5000)

    # Click thẳng vào nút menu 3 chấm sử dụng click thường, nếu lỗi thì click force
    try:
        more_btn.click(timeout=3000)
    except Exception:
        more_btn.click(force=True)
    page.wait_for_timeout(1000)

    # Đợi menu "Tải xuống" xuất hiện trong container radix menu hoạt động
    download_menu = page.locator(
        '[data-radix-menu-content] [role="menuitem"]:has-text("Tải xuống"), '
        '[data-radix-menu-content] [role="menuitem"]:has-text("Download"), '
        '.DropdownMenuContent [role="menuitem"]:has-text("Tải xuống"), '
        '.DropdownMenuContent [role="menuitem"]:has-text("Download"), '
        '[role="menu"] [role="menuitem"]:has-text("Tải xuống"), '
        '[role="menu"] [role="menuitem"]:has-text("Download")'
    ).first
    
    download_menu.wait_for(state="attached", timeout=10000)
    
    # Hover và click để mở menu chất lượng
    try:
        download_menu.hover()
        page.wait_for_timeout(300)
        download_menu.focus()
        page.wait_for_timeout(300)
        # Click để chắc chắn mở được submenu
        download_menu.click(force=True)
    except Exception:
        pass
    page.wait_for_timeout(1000)

def _download_video(page, generated_tile, quality, file_path):
    # Đăng ký bộ lắng nghe download TRƯỚC khi thao tác mở menu
    downloads = []
    page.on("download", lambda download: downloads.append(download))

    _open_download_menu(page, generated_tile)
    logging.debug(f"[Flow Video] Chọn chất lượng tải xuống: {quality}")

    # Tìm nút chất lượng tương ứng trong menu con
    quality_btn = page.locator(
        f'[data-radix-menu-content] [role="menuitem"]:has-text("{quality}"), '
        f'.DropdownMenuContent [role="menuitem"]:has-text("{quality}"), '
        f'[role="menu"] [role="menuitem"]:has-text("{quality}"), '
        f'[role="menuitem"]:has-text("{quality}")'
    ).last
    
    try:
        # Chờ nút chất lượng được gắn vào DOM và click
        quality_btn.wait_for(state="attached", timeout=5000)
        quality_btn.click(force=True)
    except Exception:
        # Không có sub-menu chất lượng hoặc không tìm thấy, click tải trực tiếp vào nút Tải xuống chính
        logging.warning(f"[Flow Video] Không tìm thấy hoặc không thể click nút chất lượng {quality}, click tải trực tiếp...")
        download_btn = page.locator(
            '[data-radix-menu-content] [role="menuitem"]:has-text("Tải xuống"), '
            '[data-radix-menu-content] [role="menuitem"]:has-text("Download"), '
            '.DropdownMenuContent [role="menuitem"]:has-text("Tải xuống"), '
            '.DropdownMenuContent [role="menuitem"]:has-text("Download"), '
            '[role="menu"] [role="menuitem"]:has-text("Tải xuống"), '
            '[role="menu"] [role="menuitem"]:has-text("Download")'
        ).first
        download_btn.click(force=True)

    # Chờ tệp được tải về máy
    deadline = time.time() + 120
    while time.time() < deadline:
        if downloads:
            downloads[0].save_as(file_path)
            return True
        page.wait_for_timeout(500)

    raise PlaywrightTimeoutError(f"Timeout chờ tải video chất lượng {quality}")


def _download_video_with_retry(page, generated_tile, quality, file_path, attempts=3):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return _download_video(page, generated_tile, quality, file_path)
        except Exception as exc:
            last_error = exc
            if "Target page, context or browser has been closed" in str(exc):
                raise
            logging.warning(
                "[Flow Video] Tải video %s lỗi lần %s/%s: %s",
                quality, attempt, attempts, exc,
            )
            if attempt < attempts:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                page.wait_for_timeout(1500)
    raise last_error


def _wait_for_video_ready(page, timeout_ms=180000):
    try:
        page.wait_for_function(
            """() => {
                const videos = Array.from(document.querySelectorAll('video'));
                if (videos.length > 0) {
                    const lastVideo = videos[videos.length - 1];
                    return lastVideo && lastVideo.src && lastVideo.readyState >= 2;
                }
                const canvases = Array.from(document.querySelectorAll('canvas'));
                return canvases.length > 0;
            }""",
            timeout=timeout_ms
        )
        return True
    except Exception as e:
        logging.warning(f"[Flow Video] Chờ video readyState thất bại: {e}")
        return False

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

                    // Flow keeps a hidden failure card inside every tile while it is generating.
                    // Only accept error text from the same visible card as a visible warning icon.
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
        logging.error(f"[Flow Video] Lỗi kiểm tra tile lỗi: {e}")
        return []


def _visible_agent_error(page):
    try:
        return page.locator("body").evaluate(
            """(body) => {
                const pattern = /Không thành công|Tác nhân đang bị quá tải|vui lòng thử lại sau vài phút|Unsuccessful|Agent is overloaded|try again in a few minutes/i;
                const visible = (el) => {
                    if (!el || !(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) return false;
                    for (let cur = el; cur && cur !== document.body; cur = cur.parentElement) {
                        const style = getComputedStyle(cur);
                        const opacity = Number.parseFloat(style.opacity);
                        if (style.display === 'none' || style.visibility === 'hidden'
                                || (!Number.isNaN(opacity) && opacity < 0.1)) return false;
                    }
                    return true;
                };
                return Array.from(body.querySelectorAll('div, span, p')).some(el => {
                    const own = Array.from(el.childNodes).filter(n => n.nodeType === Node.TEXT_NODE)
                        .map(n => n.textContent || '').join(' ').trim();
                    return own && pattern.test(own) && visible(el);
                });
            }"""
        )
    except Exception:
        return False


def _generation_progress(page, tile_id=None):
    """Read only a genuinely visible percentage; hidden failure cards are ignored."""
    try:
        return page.evaluate(
            r"""(tileId) => {
                const visible = (el) => {
                    if (!el || !(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) return false;
                    for (let cur = el; cur && cur !== document.body; cur = cur.parentElement) {
                        const style = getComputedStyle(cur);
                        const opacity = Number.parseFloat(style.opacity);
                        if (style.display === 'none' || style.visibility === 'hidden'
                                || (!Number.isNaN(opacity) && opacity < 0.1)) return false;
                    }
                    return true;
                };
                let roots = [document.body];
                if (tileId) {
                    roots = Array.from(document.querySelectorAll('[data-tile-id]'))
                        .filter(el => el.getAttribute('data-tile-id') === tileId && visible(el));
                }
                for (const root of roots) {
                    for (const el of root.querySelectorAll('div, span, p, a')) {
                        if (!visible(el)) continue;
                        const own = Array.from(el.childNodes).filter(n => n.nodeType === Node.TEXT_NODE)
                            .map(n => n.textContent || '').join(' ').trim();
                        const match = own.match(/^([0-9]{1,3})\s*%$/);
                        if (match) return Number(match[1]);
                    }
                }
                return null;
            }""",
            tile_id,
        )
    except Exception:
        return None


def _active_prompt_texts(page):
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


def _generated_video_count(page):
    try:
        # Trong chế độ video, phần tử kết quả có thể là thẻ video hoặc thẻ canvas
        vids = page.locator('video, canvas')
        visible_count = 0
        for idx in range(vids.count()):
            try:
                if vids.nth(idx).is_visible():
                    visible_count += 1
            except Exception:
                pass
        return visible_count
    except Exception:
        return 0

def run_video_fx(context, account, prompt, task_id, config):
    # Video tốn nhiều credit; tuyệt đối không tự gửi lại một request không chắc chắn.
    max_attempts = 1
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt > 1:
                logging.info(f"[Flow Video] Thử lại tạo video lần {attempt}/{max_attempts}...")
            return _run_video_fx_once(context, account, prompt, task_id, config)
        except Exception as exc:
            last_error = exc
            err_msg = str(exc)
            if ("Tài khoản đã bị đăng xuất" in err_msg or "Signed out" in err_msg
                    or "Không thể tải video" in err_msg):
                logging.error(f"[Flow Video] Bỏ qua retry với lỗi không nên tạo lại: {err_msg}")
                raise
            logging.warning(f"[Flow Video] Thử lại tạo video thất bại lần {attempt}/{max_attempts}: {err_msg}")
            if attempt >= max_attempts:
                break
            time.sleep(2)
    raise Exception(f"Flow tạo video Không thành công sau {max_attempts} lần thử: {last_error}")

def _run_video_fx_once(context, account, prompt, task_id, config):
    save_path = config.get("save_path", "")
    if not save_path or not save_path.strip():
        save_path = "output"
    final_name = config.get("final_name", str(task_id))
    if not os.path.exists(save_path):
        os.makedirs(save_path, exist_ok=True)
    _clean_error_files(save_path, final_name)

    page = context.new_page()
    try:
        logging.info(f"[Flow Video] Điều hướng tới Google Labs Flow...")
        page.goto("https://labs.google/fx/vi/tools/flow", timeout=60000)
        page.wait_for_timeout(3000)

        # 1. Tự động đóng các popup quảng cáo/chào mừng
        _close_welcome_popups(page)

        enter_flow_app(page)

        # 2. Đợi chuyển hướng SetSID
        for _ in range(30):
            if "SetSID" not in page.url and "accounts.google" not in page.url:
                break
            page.wait_for_timeout(500)

        # Tự động xử lý trang Lựa chọn tài khoản (Account Chooser)
        if "accountchooser" in page.url or "AccountChooser" in page.url:
            logging.info(f"[Flow Video] Đang tự động chọn tài khoản: {account.email}...")
            try:
                email_card = page.locator(f'text="{account.email}"').first
                if not email_card.is_visible():
                    email_card = page.locator(f'div:has-text("{account.email}"), span:has-text("{account.email}"), p:has-text("{account.email}"), li:has-text("{account.email}")').last
                if email_card.is_visible():
                    email_card.click(force=True)
                    page.wait_for_timeout(5000)
                    for _ in range(30):
                        if "SetSID" not in page.url and "accounts.google" not in page.url:
                            break
                        page.wait_for_timeout(500)
            except Exception as select_err:
                logging.warning(f"[Flow Video] Lỗi khi tự động chọn tài khoản: {select_err}")

        # Kiểm tra đăng xuất
        login_btn = page.locator('button:has-text("Đăng nhập"), button:has-text("Sign in"), a:has-text("Sign in"), a:has-text("Đăng nhập"), [aria-label*="Sign in" i]').first
        is_signed_out = False
        if login_btn.is_visible():
            is_signed_out = True
        elif "accounts.google" in page.url and ("signin" in page.url or "ServiceLogin" in page.url):
            is_signed_out = True

        if is_signed_out:
            raise Exception("Tài khoản đã bị đăng xuất (Signed out) trên Google. Vui lòng đăng nhập lại tài khoản này trên giao diện Tool.")

        # Xác định trạng thái trang
        try:
            dashboard_element = page.locator(
                'button:has-text("Dự án mới"), button:has-text("New project")'
            ).first
            work_node_element = page.locator('div:has-text("Bắt đầu tạo hoặc thả nội dung nghe nhìn"), div:has-text("Start creating or drop media"), div:has-text("Bắt đầu tạo")').last
            workspace_prompt = page.locator(
                '[role="textbox"][contenteditable="true"], textarea:not([name*="recaptcha"])'
            )
            
            found_where = None
            for _ in range(40):
                if dashboard_element.is_visible():
                    found_where = "dashboard"
                    break
                if (work_node_element.is_visible()
                        or workspace_prompt.count() > 0 and workspace_prompt.last.is_visible()):
                    found_where = "workspace"
                    break
                page.wait_for_timeout(500)

            if found_where == "dashboard":
                logging.info("[Flow Video] Khởi tạo Dự án mới...")
                
                _close_welcome_popups(page)
                try:
                    open_new_project(page, attempts=3)
                except Exception as redirect_error:
                    raise RuntimeError(
                        f"Flow không mở được dự án video mới: {redirect_error}"
                    ) from redirect_error

                if "accounts.google" in page.url and ("signin" in page.url or "ServiceLogin" in page.url):
                    raise Exception("Tài khoản đã bị đăng xuất (Signed out) trên Google. Vui lòng đăng nhập lại tài khoản này trên giao diện Tool.")

                workspace_indicator = page.locator(
                    '[role="textbox"][contenteditable="true"], textarea:not([name*="recaptcha"]), '
                    'p:has-text("Bạn muốn tạo gì?"), p:has-text("What do you want to create?"), '
                    'div:has-text("Bắt đầu tạo hoặc thả nội dung nghe nhìn"), div:has-text("Start creating or drop media"), '
                    'div:has-text("Bắt đầu tạo")'
                ).first
                try:
                    workspace_indicator.wait_for(state="visible", timeout=15000)
                except Exception:
                    pass

                try:
                    start_btn = page.locator('button:has-text("Bắt đầu"), button:has-text("Get started"), button:has-text("Bắt đầu tạo")').first
                    if start_btn.is_visible():
                        start_btn.click(force=True)
                        page.wait_for_timeout(2000)
                except Exception:
                    pass
            elif found_where == "workspace":
                logging.debug("[Flow Video] Đã ở sẵn trong workspace.")
        except Exception as e:
            if ("Tài khoản đã bị đăng xuất" in str(e)
                    or "Flow không mở được dự án" in str(e)):
                raise
            logging.debug(f"[Flow Video] Bỏ qua bước xác định trang: {e}")

        # Đóng popup quảng cáo sau khi chuyển trang
        _close_welcome_popups(page)

        # 3. Kích hoạt vùng soạn thảo
        try:
            prompt_input_preview = page.locator('textarea:not([name*="recaptcha"]), [contenteditable="true"]').last
            prompt_trigger_preview = page.locator('p:has-text("Bạn muốn tạo gì?"), p:has-text("What do you want to create?")').first
            
            for _ in range(16):
                if prompt_input_preview.is_visible() or prompt_trigger_preview.is_visible():
                    break
                page.wait_for_timeout(500)
            
            if not (prompt_input_preview.is_visible() or prompt_trigger_preview.is_visible()):
                work_node = page.locator('div:has-text("Bắt đầu tạo hoặc thả nội dung nghe nhìn"), div:has-text("Start creating or drop media"), div:has-text("Bắt đầu tạo")').last
                work_node.wait_for(state="visible", timeout=5000)
                work_node.click(force=True)
                page.wait_for_timeout(500)
                work_node.click(force=True)
                page.wait_for_timeout(500)
            
            # Đợi thêm 2 giây để các tile lịch sử tải xong hoàn chỉnh
            page.wait_for_timeout(2000)
        except Exception as e:
            logging.warning(f"[Flow Video] Lỗi click node: {e}")

        try:
            prompt_trigger = page.locator('p:has-text("Bạn muốn tạo gì?"), p:has-text("What do you want to create?")').first
            if prompt_trigger.is_visible():
                prompt_trigger.click(force=True)
                page.wait_for_timeout(1000)
        except Exception:
            pass

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
                    logging.info("[Flow Video] Phát hiện chế độ Tác nhân (Agent) đang bật. Đang click để tắt...")
                    agent_btn.click(force=True)
                    page.wait_for_timeout(1500)
                    # Click lại prompt input để kích hoạt hiển thị nút cấu hình sau khi tắt Agent
                    prompt_input.click(force=True)
                    page.wait_for_timeout(1000)
                
            # Điền prompt
            logging.info(f"[Flow Video] Điền prompt: '{prompt}'")
            prompt_input.fill(prompt)
            page.wait_for_timeout(1000)
        except Exception as e:
            raise RuntimeError(f"Không thể điền prompt video vào Google Flow: {e}") from e

        # 5. Cấu hình theo đúng giao diện Flow đang hiển thị.
        configure_generation(page, prompt_input, "video", config)

        # 6. Nhấn Tạo video
        try:
            logging.info("[Flow Video] Nhấn nút Tạo...")
            
            # Lấy danh sách tile ID hiện tại trước khi click Tạo
            existing_tiles = set(page.locator('[data-tile-id]').evaluate_all(
                'elements => elements.map(el => el.getAttribute("data-tile-id"))'
            ))
            logging.debug(f"[Flow Video] Các tile hiện tại trước khi tạo: {existing_tiles}")

            submitted = False
            new_tile_id = None
            video_baseline = _generated_video_count(page)
            for send_attempt in range(1, 4):
                click_generate(page, prompt_input)
                ack_deadline = time.time() + 12
                while time.time() < ack_deadline:
                    current_tiles = set(page.locator('[data-tile-id]').evaluate_all(
                        'elements => elements.map(el => el.getAttribute("data-tile-id"))'
                    ))
                    new_tiles = current_tiles - existing_tiles
                    if new_tiles:
                        new_tile_id = next(iter(new_tiles))
                        submitted = True
                        break
                    if (_generated_video_count(page) > video_baseline
                            or _generation_progress(page) is not None):
                        submitted = True
                        break
                    if generation_is_busy(page):
                        logging.info(
                            "[Flow Video] Web đã nhận prompt và đang diễn giải; chuyển sang vòng chờ tạo video."
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
                    "[Flow Video] Web chưa nhận lần bấm Tạo %s/3; prompt vẫn còn, bấm lại an toàn.",
                    send_attempt,
                )
            if not submitted:
                raise FlowGenerationFailed(
                    "Flow không nhận prompt video sau 3 lần bấm Tạo; chưa phát sinh tile/% tiến độ."
                )
            generation_deadline = time.time() + 600
        except Exception as e:
            logging.error(f"[Flow Video] Không thể nhấn nút Tạo: {e}")
            raise FlowGenerationFailed(f"Không thể gửi prompt để tạo video: {e}") from e

        # Chờ tile mới xuất hiện
        logging.info("[Flow Video] Đang chờ tile video mới xuất hiện...")
        while new_tile_id is None and time.time() < generation_deadline:
            current_tiles = set(page.locator('[data-tile-id]').evaluate_all(
                'elements => elements.map(el => el.getAttribute("data-tile-id"))'
            ))
            new_tiles = current_tiles - existing_tiles
            if new_tiles:
                new_tile_id = list(new_tiles)[0]
                logging.info(f"[Flow Video] Đã phát hiện tile mới: {new_tile_id}")
                break
            page.wait_for_timeout(1000)

        if not new_tile_id and _generation_progress(page) is None:
            if generation_is_busy(page):
                raise RuntimeError(
                    "Flow vẫn đang diễn giải/tạo video sau 10 phút; không tự gửi lại để tránh trừ credit trùng."
                )
            raise FlowGenerationFailed(
                "Không tìm thấy tile video mới sau khi gửi prompt; không dùng lại tile cũ."
            )

        # Chờ tạo video hoàn tất trên tile mới này
        logging.info(
            "[Flow Video] Đang đợi video%s được tạo hoàn chỉnh...",
            f" trong tile {new_tile_id}" if new_tile_id else "",
        )
        
        # Veo có thể đứng ở 99% trong hơn 4 phút khi hệ thống đông. Chờ tối đa
        # 10 phút nhưng không tự gửi lại request để tránh tạo trùng/trừ credit.
        video_ready = False
        last_progress = None
        while time.time() < generation_deadline:
            progress = _generation_progress(page, new_tile_id)
            if progress is not None and progress != last_progress:
                logging.info("[Flow Video] Video đang được tạo: %s%%; tiếp tục chờ...", progress)
                last_progress = progress
            
            # Kiểm tra xem video đã sẵn sàng chưa trong tile (đã có video src)
            is_ready = page.evaluate(
                """(tileId) => {
                    const tile = tileId
                        ? document.querySelector(`[data-tile-id="${tileId}"]`)
                        : document.body;
                    if (!tile) return false;
                    
                    const video = tile.querySelector('video');
                    let hasVideo = false;
                    if (video) {
                        const videoSrc = video.src || '';
                        const source = video.querySelector('source');
                        const sourceSrc = source ? source.src : '';
                        hasVideo = (videoSrc.length > 0) || (sourceSrc.length > 0);
                    }
                    
                    const text = (tile.textContent || "").toLowerCase();
                    const isGenerating = text.includes("%") || text.includes("đang tạo") || text.includes("generating") || text.includes("chuẩn bị") || text.includes("preparing");
                    
                    return hasVideo && !isGenerating;
                }""",
                new_tile_id
            )
            if is_ready:
                video_ready = True
                logging.info(f"[Flow Video] Video trong tile {new_tile_id} đã sẵn sàng!")
                break

            # A visible percentage always wins over the hidden failure card that
            # Flow keeps mounted during generation.
            if progress is None:
                failed_ids = _generation_failed_tile_ids(page, visible_only=True)
                tile_failed = bool(new_tile_id and new_tile_id in failed_ids)
                if tile_failed or _visible_agent_error(page):
                    raise FlowGenerationFailed(
                        f"Google Labs Flow báo lỗi tạo video trên tile {new_tile_id or 'chưa gán ID'}."
                    )
                
            page.wait_for_timeout(2000)
            
        if not video_ready:
            if _generation_progress(page, new_tile_id) is not None:
                raise RuntimeError(
                    "Flow vẫn đang tạo video sau 10 phút; không tự retry để tránh trừ credit/tạo trùng."
                )
            raise FlowGenerationFailed(f"Flow không hoàn tất video sau 10 phút trên tile {new_tile_id}.")

        # 7. Đặt tile container kết quả chính xác bằng ID của tile mới (tránh trùng lặp ID gây lỗi strict mode)
        generated_tiles = (page.locator(f'[data-tile-id="{new_tile_id}"]')
                           if new_tile_id else page.locator("body"))
        generated_tile = None
        try:
            for i in range(generated_tiles.count()):
                tile_cand = generated_tiles.nth(i)
                if tile_cand.locator('video, canvas').count() > 0 or tile_cand.locator('button:has-text("more_vert"), [aria-label*="more" i]').count() > 0:
                    generated_tile = tile_cand
                    break
        except Exception:
            pass
        if not generated_tile:
            generated_tile = generated_tiles.first
            
        logging.info("[Flow Video] Đã tạo video thành công và sẵn sàng để tải xuống!")
        page.wait_for_timeout(2000)

        # 8. Tải video
        # Kiểm tra lại xem tile có báo lỗi không
        failed_ids = _generation_failed_tile_ids(page, visible_only=True)
        if (_generation_progress(page, new_tile_id) is None
                and new_tile_id and new_tile_id in failed_ids):
            raise FlowGenerationFailed(f"Google Labs Flow báo lỗi tạo video trên tile {new_tile_id}.")

        # Xác định chất lượng video
        qualities = config.get("quality", ["720p"])
        target_quality = "720p"
        if "4K" in qualities:
            target_quality = "4K"
        elif "1080p" in qualities:
            target_quality = "1080p"

        file_path = _quality_file_path(save_path, final_name, target_quality)

        try:
            # Hover vào tile container và click tải xuống
            downloaded = _download_video_with_retry(
                page, generated_tile, target_quality, file_path, attempts=3
            )
            if not downloaded and target_quality != "720p":
                # Fallback chất lượng thấp hơn
                fallback_quality = "720p"
                fallback_path = _quality_file_path(save_path, final_name, fallback_quality)
                logging.warning(f"[Flow Video] Thử tải fallback {fallback_quality}...")
                _download_video_with_retry(
                    page, generated_tile, fallback_quality, fallback_path, attempts=3
                )
                file_path = fallback_path
            
            logging.info(f"[Flow Video] Đã lưu video thành công vào: {file_path}")
            _clean_error_files(save_path, final_name)
            try:
                from core.browser_manager import update_account_credits_and_type_from_page
                update_account_credits_and_type_from_page(page, account.id)
            except Exception as cu_err:
                logging.warning(f"[Flow Video] Không thể cập nhật credits: {cu_err}")
            page.close()
            return file_path
        except Exception as e:
            logging.warning(f"[Flow Video] Lỗi menu tải xuống: {e}. Thử fallback download qua thẻ source...")

        # Fallback lấy url video trực tiếp qua thẻ source/src
        try:
            video_el = generated_tile.locator('video').first
            if video_el.count() == 0:
                logging.info("[Flow Video] Không tìm thấy thẻ video trong tile, tìm kiếm thẻ video toàn cục trên trang...")
                video_el = page.locator('video').first
                
            src_url = None
            if video_el.count() > 0:
                src_url = video_el.get_attribute("src")
                if not src_url:
                    source_el = video_el.locator('source').first
                    if source_el.count() > 0:
                        src_url = source_el.get_attribute("src")

            if src_url:
                # Nếu là URL tương đối, ghép thêm domain gốc
                if src_url.startswith("/"):
                    src_url = "https://labs.google" + src_url
                elif not src_url.startswith("http"):
                    src_url = "https://labs.google/fx/" + src_url
                    
                import requests
                logging.info(f"[Flow Video] Tải qua URL trực tiếp: {src_url}")
                
                # Copy cookies từ context để truyền vào request session
                cookies_list = context.cookies()
                session = requests.Session()
                for cookie in cookies_list:
                    session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'])
                
                pointer = session.get(src_url)
                with open(file_path, "wb") as f:
                    f.write(pointer.content)
                logging.info(f"[Flow Video] Đã lưu video qua src thành công: {file_path}")
                _clean_error_files(save_path, final_name)
                try:
                    from core.browser_manager import update_account_credits_and_type_from_page
                    update_account_credits_and_type_from_page(page, account.id)
                except Exception as cu_err:
                    logging.warning(f"[Flow Video] Không thể cập nhật credits: {cu_err}")
                page.close()
                return file_path
            else:
                raise Exception("Không tìm thấy thuộc tính src trên thẻ video/source kết quả.")
        except Exception as err:
            try:
                debug_path = "debug_video_error.html"
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(page.content())
                if os.path.exists(debug_path):
                    os.remove(debug_path)
            except:
                pass
            raise Exception(f"Không thể tải video: {err}")

    except FlowGenerationFailed:
        try:
            if "accounts.google.com" in page.url or "signin" in page.url:
                page.close()
                raise Exception("Tài khoản đã bị đăng xuất (Signed out) trên Google. Vui lòng đăng nhập lại tài khoản này trên giao diện Tool.")
            err_img_path = os.path.join(save_path, f"error_{final_name}_fail.png")
            err_html_path = os.path.join(save_path, f"error_{final_name}_fail.html")
            page.screenshot(path=err_img_path)
            with open(err_html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            # Giữ lại file error để người dùng kiểm tra lỗi
        except Exception as se:
            if "Tài khoản đã bị đăng xuất" in str(se):
                raise
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
            with open(err_html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            # Giữ lại file error để người dùng kiểm tra lỗi
        except Exception as se:
            if "Tài khoản đã bị đăng xuất" in str(se):
                raise
        page.close()
        raise Exception(f"Lỗi kịch bản Flow Video: {str(e)}")
