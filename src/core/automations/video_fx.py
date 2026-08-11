import os
import time
import logging
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

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
    for keyword in close_keywords:
        try:
            btns = page.locator(f'button:has-text("{keyword}"), [aria-label*="{keyword}" i]').all()
            for btn in btns:
                if btn.is_visible():
                    logging.info(f"[Flow Video] Tự động click nút đóng popup: {keyword}")
                    btn.click(force=True)
                    page.wait_for_timeout(500)
        except Exception:
            pass

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
                        if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) {
                            return false;
                        }
                        parent = parent.parentElement;
                    }
                    return true;
                };

                return tiles.filter(tile => {
                    const visible = isRealVisible(tile);
                    if (visibleOnly && !visible) return false;

                    const hasWarningIcon = Array.from(tile.querySelectorAll("i")).some(icon => {
                        const iconName = (icon.textContent || "").trim().toLowerCase();
                        const iconVisible = isRealVisible(icon);
                        return (!visibleOnly || iconVisible) && (iconName === "warning" || iconName === "warning_amber" || iconName === "error");
                    });

                    const hasErrorText = Array.from(tile.querySelectorAll("span, div, p, a")).some(el => {
                        const textContent = (el.textContent || el.innerText || "").toLowerCase();
                        const elVisible = isRealVisible(el);
                        if (visibleOnly && !elVisible) return false;
                        return keywords.some(kw => textContent.includes(kw.toLowerCase()));
                    });

                    return hasWarningIcon && hasErrorText;
                }).map(tile => tile.getAttribute("data-tile-id") || "");
            }""",
            { "visibleOnly": visible_only, "keywords": keywords }
        )
    except Exception as e:
        logging.error(f"[Flow Video] Lỗi kiểm tra tile lỗi: {e}")
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

def clear_browser_history(page):
    logging.info("[Flow Video] Đang thực hiện xóa lịch sử duyệt web 1 giờ qua...")
    try:
        page.goto("chrome://settings/clearBrowserData?search=cook", timeout=15000)
        page.wait_for_timeout(3000)
        page.keyboard.press("Enter")
        page.wait_for_timeout(5000)
        logging.info("[Flow Video] Đã xóa lịch sử duyệt web thành công!")
    except Exception as e:
        logging.warning(f"[Flow Video] Không thể xóa lịch sử duyệt web tự động: {e}")

def run_video_fx(context, account, prompt, task_id, config):
    max_attempts = 5
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt > 1:
                logging.info(f"[Flow Video] Thử lại tạo video lần {attempt}/{max_attempts}...")
            return _run_video_fx_once(context, account, prompt, task_id, config)
        except Exception as exc:
            last_error = exc
            err_msg = str(exc)
            if "Tài khoản đã bị đăng xuất" in err_msg or "Signed out" in err_msg:
                logging.error(f"[Flow Video] Bỏ qua retry do tài khoản đã bị đăng xuất: {err_msg}")
                raise
            logging.warning(f"[Flow Video] Thử lại tạo video thất bại lần {attempt}/{max_attempts}: {err_msg}")
            if attempt >= max_attempts:
                break
            if attempt == 3:
                try:
                    temp_page = context.new_page()
                    clear_browser_history(temp_page)
                    temp_page.close()
                except Exception as clear_err:
                    logging.warning(f"[Flow Video] Lỗi khi mở trang xóa lịch sử: {clear_err}")
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
        public_cta = page.locator('button:has-text("Create with Google Flow"), button:has-text("Explore Tools"), button:has-text("Create with Flow")').first
        profile_img = page.locator('button:has(img[alt*="hồ sơ" i]), button:has(img[alt*="profile" i]), button:has(img)').first

        is_signed_out = False
        if login_btn.is_visible():
            is_signed_out = True
        elif "accounts.google" in page.url and ("signin" in page.url or "ServiceLogin" in page.url):
            is_signed_out = True
        elif public_cta.is_visible() and not profile_img.is_visible():
            is_signed_out = True

        if is_signed_out:
            raise Exception("Tài khoản đã bị đăng xuất (Signed out) trên Google. Vui lòng đăng nhập lại tài khoản này trên giao diện Tool.")

        # Xác định trạng thái trang
        try:
            dashboard_element = page.locator('button:has-text("Dự án mới"), button:has-text("New project"), button:has-text("add_2"), button:has-text("Bắt đầu"), button:has-text("Get started")').first
            work_node_element = page.locator('div:has-text("Bắt đầu tạo hoặc thả nội dung nghe nhìn"), div:has-text("Start creating or drop media"), div:has-text("Bắt đầu tạo")').last
            
            found_where = None
            for _ in range(40):
                if dashboard_element.is_visible():
                    found_where = "dashboard"
                    break
                if work_node_element.is_visible():
                    found_where = "workspace"
                    break
                page.wait_for_timeout(500)

            if found_where == "dashboard":
                logging.info("[Flow Video] Khởi tạo Dự án mới...")
                
                # Click và chờ chuyển hướng dự án mới
                redirected = False
                for click_attempt in range(6):
                    _close_welcome_popups(page)
                    try:
                        dashboard_element.click(force=True)
                    except Exception:
                        pass
                    page.wait_for_timeout(1500)
                    if "project=" in page.url:
                        redirected = True
                        logging.info("[Flow Video] Đã chuyển hướng thành công vào URL dự án mới.")
                        break
                
                if not redirected:
                    logging.warning("[Flow Video] Không nhận diện được chuyển hướng URL dự án mới, tiếp tục chạy...")

                if "accounts.google" in page.url and ("signin" in page.url or "ServiceLogin" in page.url):
                    raise Exception("Tài khoản đã bị đăng xuất (Signed out) trên Google. Vui lòng đăng nhập lại tài khoản này trên giao diện Tool.")

                workspace_indicator = page.locator(
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
            if "Tài khoản đã bị đăng xuất" in str(e):
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
            prompt_input = None
            prompt_locs = page.locator('textarea:not([name*="recaptcha"]), [contenteditable="true"]')
            for idx in range(prompt_locs.count()):
                loc = prompt_locs.nth(idx)
                if loc.is_visible():
                    prompt_input = loc
            
            if not prompt_input:
                import re
                prompt_input = page.get_by_placeholder(re.compile(r"Bạn muốn tạo gì|What do you want to create", re.I)).first
            
            if not prompt_input or not prompt_input.is_visible():
                prompt_input = page.locator('textarea:not([name*="recaptcha"]), [contenteditable="true"]').last
            
            prompt_input.wait_for(state="visible", timeout=15000)
            prompt_input.click(force=True)
            page.wait_for_timeout(500)
            
            # Kiểm tra trạng thái Agent qua nút Agent hiển thị thực tế và thuộc tính aria-pressed
            agent_btn = page.locator('button:has-text("Tác nhân"), button:has-text("Agent")').first
            try:
                agent_btn.wait_for(state="attached", timeout=3000)
            except:
                pass
                
            if agent_btn.count() > 0:
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
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            prompt_input.fill(prompt)
            page.wait_for_timeout(1000)
        except Exception as e:
            logging.warning(f"[Flow Video] Lỗi điền prompt hoặc xử lý Tác nhân: {e}")
            try:
                page.keyboard.type(prompt)
                page.wait_for_timeout(1000)
            except:
                pass

        # 5. Cấu hình Model, Tỷ lệ video, Chế độ Video
        try:
            logging.info("[Flow Video] Mở panel cấu hình...")
            # Lọc tìm nút config hiển thị thực tế
            config_btn = None
            config_locs = page.locator(
                'button:has-text("Banana"), button:has-text("Nano"), button:has-text("Imagen"), '
                'button:has-text("🍌"), button:has-text("Veo"), button:has-text("Omni"), '
                'button:has-text("Veo3.1"), button:has-text("Flash"), button:has-text("Lite"), '
                'button:has-text("Fast"), button:has-text("Quality")'
            )
            for idx in range(config_locs.count()):
                loc = config_locs.nth(idx)
                if loc.is_visible():
                    config_btn = loc
                    break
            if not config_btn:
                config_btn = config_locs.first
            
            # Cải tiến: Nếu nút config chính chưa sẵn sàng, click vào vùng trống để focus rồi check lại
            try:
                config_btn.wait_for(state="visible", timeout=6000)
            except Exception:
                logging.debug("[Flow Video] Nút config chính chưa thấy, thử click vùng nhập prompt để trigger...")
                prompt_input.click(force=True)
                page.wait_for_timeout(1000)
                config_btn.wait_for(state="visible", timeout=6000)
                
            config_btn.click(force=True)
            
            # Đợi panel cấu hình hiển thị
            config_panel = page.locator('[data-radix-menu-content], .DropdownMenuContent, [role="menu"]').first
            config_panel.wait_for(state="visible", timeout=10000)
            page.wait_for_timeout(1000)

            # A. Chuyển tab sang VIDEO
            try:
                # Tìm tab Video bằng text trong panel cấu hình
                video_tab = page.locator(
                    '[data-radix-menu-content] button[role="tab"]:has-text("Video"), '
                    '[data-radix-menu-content] button:has-text("Video"), '
                    'button[role="tab"]:has-text("Video")'
                ).first
                video_tab.wait_for(state="visible", timeout=5000)
                video_tab.click(force=True)
                logging.info("[Flow Video] Đã chọn chế độ tạo: Video")
                page.wait_for_timeout(1000)
            except Exception as ex:
                logging.warning(f"[Flow Video] Không chuyển được sang tab Video: {ex}")

            # B. Chọn Model bằng từ khóa (để tránh lệch dấu cách hoặc credit text)
            selected_model = config.get("model", "Veo 3.1 - Fast [20 Credit]")
            model_keyword = "Fast"
            if "Lite" in selected_model:
                model_keyword = "Lite"
            elif "Flash" in selected_model or "Omni" in selected_model:
                model_keyword = "Omni"
            elif "Quality" in selected_model:
                model_keyword = "Quality"

            # Click mở menu model (nút dropdown trong popup cấu hình)
            try:
                model_dropdown_trigger = page.locator(
                    '[data-radix-menu-content] button[aria-haspopup="menu"], '
                    '.DropdownMenuContent button[aria-haspopup="menu"]'
                ).first
                model_dropdown_trigger.wait_for(state="visible", timeout=5000)
                
                current_model_text = model_dropdown_trigger.inner_text()
                if model_keyword.lower() in current_model_text.lower() or (model_keyword == "Omni" and "flash" in current_model_text.lower()):
                    logging.info(f"[Flow Video] Model chứa từ khóa '{model_keyword}' đã được chọn sẵn.")
                else:
                    model_dropdown_trigger.click(force=True)
                    page.wait_for_timeout(1000)

                    # Chọn option model tương ứng
                    model_option = page.locator(
                        f'[role="menuitem"]:has-text("{model_keyword}"), '
                        f'[role="option"]:has-text("{model_keyword}"), '
                        f'button:has-text("{model_keyword}"), '
                        f'span:has-text("{model_keyword}")'
                    ).last
                    
                    if model_keyword == "Omni":
                        model_option = page.locator(
                            '[role="menuitem"]:has-text("Omni"), [role="menuitem"]:has-text("Flash"), '
                            '[role="option"]:has-text("Omni"), [role="option"]:has-text("Flash"), '
                            'button:has-text("Omni"), button:has-text("Flash"), '
                            'span:has-text("Omni"), span:has-text("Flash")'
                        ).last

                    model_option.wait_for(state="visible", timeout=5000)
                    model_option.click(force=True)
                    logging.info(f"[Flow Video] Đã đổi model sang: {model_keyword}")
                    page.wait_for_timeout(1000)
            except Exception as ex:
                logging.warning(f"[Flow Video] Không chọn được model với từ khóa {model_keyword}: {ex}")

            # C. Chọn Tỷ lệ video
            aspect_ratio = config.get("aspect_ratio", "16:9 Ngang")
            ratio_pattern = "16:9" if "16:9" in aspect_ratio else "9:16"
            try:
                ratio_tab = page.locator(
                    f'[data-radix-menu-content] button[role="tab"]:has-text("{ratio_pattern}"), '
                    f'.DropdownMenuContent button:has-text("{ratio_pattern}"), '
                    f'button:has-text("{ratio_pattern}")'
                ).first
                ratio_tab.click(force=True)
                logging.info(f"[Flow Video] Đã chọn tỷ lệ video: {ratio_pattern}")
                page.wait_for_timeout(500)
            except Exception as ex:
                logging.warning(f"[Flow Video] Không chọn được tỷ lệ video {ratio_pattern}: {ex}")

            # D. Chọn Số lượng video (ở đây mặc định dùng 1x)
            try:
                count_tab = page.locator(
                    '[data-radix-menu-content] button[role="tab"]:has-text("1x"), '
                    '.DropdownMenuContent button:has-text("1x"), '
                    'button:has-text("1x")'
                ).first
                count_tab.click(force=True)
                page.wait_for_timeout(500)
            except Exception as ex:
                logging.warning(f"[Flow Video] Không chọn được số lượng 1x: {ex}")

        except Exception as e:
            logging.warning(f"[Flow Video] Lỗi cấu hình nâng cao: {e}")

        # 6. Nhấn Tạo video
        try:
            logging.info("[Flow Video] Nhấn nút Tạo...")
            
            # Lấy danh sách tile ID hiện tại trước khi click Tạo
            existing_tiles = set(page.locator('[data-tile-id]').evaluate_all(
                'elements => elements.map(el => el.getAttribute("data-tile-id"))'
            ))
            logging.debug(f"[Flow Video] Các tile hiện tại trước khi tạo: {existing_tiles}")

            # Gửi prompt bằng phím tắt Control+Enter trực tiếp trên ô prompt
            prompt_input.click(force=True)
            page.wait_for_timeout(500)
            prompt_input.press("Control+Enter")
            page.wait_for_timeout(2000)
        except Exception as e:
            logging.warning(f"[Flow Video] Lỗi gửi prompt bằng phím tắt: {e}. Thử click nút Tạo...")
            try:
                create_btn = page.locator('button:has(i:has-text("arrow_forward"))').first
                if not create_btn.is_visible():
                    create_btn = page.locator('button:has(span:has-text("Tạo")), button:has(span:has-text("Generate")), button:has(span:has-text("Create"))').first
                create_btn.wait_for(state="visible", timeout=10000)
                create_btn.click(force=True)
                page.wait_for_timeout(2000)
            except Exception as click_err:
                logging.error(f"[Flow Video] Không thể nhấn nút Tạo: {click_err}")
                raise FlowGenerationFailed("Không thể gửi prompt để tạo video.")

        # Chờ tile mới xuất hiện
        new_tile_id = None
        logging.info("[Flow Video] Đang chờ tile video mới xuất hiện...")
        tile_deadline = time.time() + 20
        while time.time() < tile_deadline:
            current_tiles = set(page.locator('[data-tile-id]').evaluate_all(
                'elements => elements.map(el => el.getAttribute("data-tile-id"))'
            ))
            new_tiles = current_tiles - existing_tiles
            if new_tiles:
                new_tile_id = list(new_tiles)[0]
                logging.info(f"[Flow Video] Đã phát hiện tile mới: {new_tile_id}")
                break
            page.wait_for_timeout(1000)

        if not new_tile_id:
            # Nếu không thấy tile mới xuất hiện, fallback lấy tile cuối cùng
            last_tile = page.locator('[data-tile-id]').last
            if last_tile.count() > 0:
                new_tile_id = last_tile.get_attribute("data-tile-id")
                logging.warning(f"[Flow Video] Không phát hiện tile mới bằng so khớp ID, fallback dùng tile cuối cùng: {new_tile_id}")
            else:
                raise FlowGenerationFailed("Không tìm thấy bất kỳ tile nào trên trang sau khi nhấn Tạo.")

        # Chờ tạo video hoàn tất trên tile mới này
        logging.info(f"[Flow Video] Đang đợi video trong tile {new_tile_id} được tạo hoàn chỉnh...")
        
        # Chờ tạo video hoàn tất (Veo có thể tốn từ 30s tới 4 phút tùy chất lượng và server)
        deadline = time.time() + 240
        video_ready = False
        while time.time() < deadline:
            # Kiểm tra xem tile mới này có bị lỗi không
            failed_ids = _generation_failed_tile_ids(page, visible_only=True)
            if new_tile_id in failed_ids:
                raise FlowGenerationFailed(f"Google Labs Flow báo lỗi tạo video trên tile {new_tile_id}.")
            
            # Kiểm tra xem video đã sẵn sàng chưa trong tile (đã có video src)
            is_ready = page.evaluate(
                """(tileId) => {
                    const tile = document.querySelector(`[data-tile-id="${tileId}"]`);
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
                
            page.wait_for_timeout(2000)
            
        if not video_ready:
            logging.warning(f"[Flow Video] Quá thời gian chờ video readyState trên tile {new_tile_id}, tiến hành tải fallback...")

        # 7. Đặt tile container kết quả chính xác bằng ID của tile mới (tránh trùng lặp ID gây lỗi strict mode)
        generated_tiles = page.locator(f'[data-tile-id="{new_tile_id}"]')
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
        if new_tile_id in failed_ids:
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
            downloaded = _download_video(page, generated_tile, target_quality, file_path)
            if not downloaded and target_quality != "720p":
                # Fallback chất lượng thấp hơn
                fallback_quality = "720p"
                fallback_path = _quality_file_path(save_path, final_name, fallback_quality)
                logging.warning(f"[Flow Video] Thử tải fallback {fallback_quality}...")
                _download_video(page, generated_tile, fallback_quality, fallback_path)
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
