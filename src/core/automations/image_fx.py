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
    for keyword in close_keywords:
        try:
            btns = page.locator(f'button:has-text("{keyword}"), [aria-label*="{keyword}" i]').all()
            for btn in btns:
                if btn.is_visible():
                    logging.info(f"[Flow] Tự động click nút đóng popup: {keyword}")
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


def _generation_failed_visible(page):
    return _generation_failed_count(page) > 0


def _generation_failed_count(page):
    return len(_generation_failed_tile_ids(page, visible_only=True))


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


def clear_browser_history(page):
    """
    Xóa lịch sử duyệt web 1 giờ qua ngầm bằng cách mở trang cài đặt Chrome và nhấn Enter.
    """
    logging.info("[Flow] Đang thực hiện xóa lịch sử duyệt web 1 giờ qua...")
    try:
        page.goto("chrome://settings/clearBrowserData?search=cook", timeout=15000)
        page.wait_for_timeout(3000)
        
        # Nhấn Enter để thực hiện xóa dữ liệu duyệt web
        page.keyboard.press("Enter")
        page.wait_for_timeout(5000) # Đợi 5 giây cho quá trình xóa dữ liệu hoàn tất
        logging.info("[Flow] Đã xóa lịch sử duyệt web thành công!")
    except Exception as e:
        logging.warning(f"[Flow] Không thể xóa lịch sử duyệt web tự động: {e}")

def run_image_fx(context, account, prompt, task_id, config):
    max_attempts = 5 # Tổng cộng 5 lần thử
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt > 1:
                logging.info(f"[Flow] Thử lại tạo ảnh lần {attempt}/{max_attempts}...")
            return _run_image_fx_once(context, account, prompt, task_id, config)
        except Exception as exc:
            last_error = exc
            err_msg = str(exc)
            if "Tài khoản đã bị đăng xuất" in err_msg or "Signed out" in err_msg:
                logging.error(f"[Flow] Bỏ qua retry do tài khoản đã bị đăng xuất: {err_msg}")
                raise
            
            logging.warning(f"[Flow] Thử lại tạo ảnh thất bại lần {attempt}/{max_attempts}: {err_msg}")
            if attempt >= max_attempts:
                break
                
            # Nếu đã thử 3 lần mà vẫn thất bại -> thực hiện xóa lịch sử 1 giờ qua trước khi sang lần 4, 5
            if attempt == 3:
                try:
                    temp_page = context.new_page()
                    clear_browser_history(temp_page)
                    temp_page.close()
                except Exception as clear_err:
                    logging.warning(f"[Flow] Lỗi khi mở trang xóa lịch sử: {clear_err}")
            
            # Đợi 2 giây trước khi sang lần thử tiếp theo
            time.sleep(2)
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

        try:
            logging.info("[Flow] Đang xác định trạng thái trang...")
            dashboard_element = page.locator('button:has-text("Dự án mới"), button:has-text("New project"), button:has-text("add_2"), button:has-text("Bắt đầu"), button:has-text("Get started")').first
            work_node_element = page.locator('div:has-text("Bắt đầu tạo hoặc thả nội dung nghe nhìn"), div:has-text("Start creating or drop media"), div:has-text("Bắt đầu tạo")').last
            
            found_where = None
            for _ in range(40): # Tối đa 20 giây
                if dashboard_element.is_visible():
                    found_where = "dashboard"
                    break
                if work_node_element.is_visible():
                    found_where = "workspace"
                    break
                page.wait_for_timeout(500)
                
            if found_where == "dashboard":
                logging.info("[Flow] Phát hiện đang ở trang chủ/dashboard. Khởi tạo Dự án mới...")
                
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
                        logging.info("[Flow] Đã chuyển hướng thành công vào URL dự án mới.")
                        break
                
                if not redirected:
                    logging.warning("[Flow] Không nhận diện được chuyển hướng URL dự án mới, tiếp tục chạy...")

                if "accounts.google" in page.url and ("signin" in page.url or "ServiceLogin" in page.url):
                    raise Exception("Tài khoản đã bị đăng xuất (Signed out) trên Google. Vui lòng đăng nhập lại tài khoản này trên giao diện Tool.")

                # Đợi trang workspace mới tải xong (chờ thanh prompt hoặc work node xuất hiện)
                workspace_indicator = page.locator(
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
            if "Tài khoản đã bị đăng xuất" in str(e):
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
                    logging.info("[Flow] Phát hiện chế độ Tác nhân (Agent) đang bật. Đang click để tắt...")
                    agent_btn.click(force=True)
                    page.wait_for_timeout(1500)
                    # Click lại prompt input để kích hoạt hiển thị nút cấu hình sau khi tắt Agent
                    prompt_input.click(force=True)
                    page.wait_for_timeout(1000)
                
            # Điền prompt
            logging.info(f"[Flow] Điền prompt: '{prompt}'")
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            prompt_input.fill(prompt)
            page.wait_for_timeout(1000)
        except Exception as e:
            logging.warning(f"[Flow] Không dùng fill() được: {e}. Gõ trực tiếp bằng keyboard...")
            page.keyboard.type(prompt)
            page.wait_for_timeout(1000)

        # 5. Cấu hình Model, Tỷ lệ ảnh, Số lượng ảnh
        try:
            logging.info("[Flow] Thiết lập Model và Tỉ lệ ảnh...")
            # Lọc tìm nút config hiển thị thực tế
            config_btn = None
            config_locs = page.locator('button:has-text("Banana"), button:has-text("Nano"), button:has-text("Imagen"), button:has-text("🍌")')
            for idx in range(config_locs.count()):
                loc = config_locs.nth(idx)
                if loc.is_visible():
                    config_btn = loc
                    break
            if not config_btn:
                config_btn = config_locs.first
            
            # 5a. Chọn Model - kiểm tra model hiện tại trước khi chọn
            selected_model = config.get("model", "Nano Banana 2")
            flow_model_name = selected_model
            if "Nano Banana 2" in selected_model:
                flow_model_name = "Nano Banana 2"
            elif "Nano Banana Pro" in selected_model:
                flow_model_name = "Nano Banana Pro"
            elif "Imagen 4" in selected_model:
                flow_model_name = "Imagen 4"
            
            # Đọc text trên nút config để biết model hiện tại
            current_btn_text = config_btn.inner_text() if config_btn.is_visible() else ""
            logging.debug(f"[Flow] Model hiện tại trên nút: '{current_btn_text}', Model cần chọn: '{flow_model_name}'")
            
            if flow_model_name in current_btn_text:
                logging.debug(f"[Flow] Model '{flow_model_name}' đã được chọn sẵn. Bỏ qua bước chọn model.")
            else:
                logging.debug(f"[Flow] Cần đổi model sang '{flow_model_name}'. Mở dropdown...")
                config_btn.click(force=True)
                page.wait_for_timeout(1500)
                try:
                    model_dropdown = page.locator('button:has-text("arrow_drop_down")').first
                    model_dropdown.click(force=True)
                    page.wait_for_timeout(1000)
                    
                    model_item = page.locator(f'[role="menuitem"]:has-text("{flow_model_name}"), [role="option"]:has-text("{flow_model_name}")').first
                    model_item.click(force=True)
                    page.wait_for_timeout(1000)
                except Exception as ex:
                    logging.warning(f"[Flow] Không chọn được model {flow_model_name}: {ex}")
            
            # Mở panel cấu hình (nếu chưa mở) để chọn số lượng và tỷ lệ
            config_btn.click(force=True)
            page.wait_for_timeout(1500)

            # 5b. Chọn số lượng ảnh
            images_count = config.get("images_per_prompt", 1)
            tab_count_name = f"{images_count}x"
            try:
                count_tab = page.locator(f'[role="tab"]:has-text("{tab_count_name}"), button:has-text("{tab_count_name}")').first
                if not count_tab.is_visible():
                    logging.debug("[Flow] Menu cấu hình bị đóng, đang mở lại để chọn số lượng...")
                    config_btn.click(force=True)
                    page.wait_for_timeout(1000)
                count_tab.click(force=True)
                page.wait_for_timeout(500)
            except Exception as ex:
                logging.warning(f"[Flow] Không chọn được số lượng {tab_count_name}: {ex}")

            # 5c. Chọn tỷ lệ ảnh
            aspect_ratio = config.get("aspect_ratio", "16:9 Ngang")
            ratio_pattern = r"16\s*:\s*9"
            if "16:9" in aspect_ratio:
                ratio_pattern = r"16\s*:\s*9"
            elif "9:16" in aspect_ratio:
                ratio_pattern = r"9\s*:\s*16"
            elif "1:1" in aspect_ratio:
                ratio_pattern = r"1\s*:\s*1"
            elif "4:3" in aspect_ratio:
                ratio_pattern = r"4\s*:\s*3"
            elif "3:4" in aspect_ratio:
                ratio_pattern = r"3\s*:\s*4"
                
            try:
                import re
                ratio_tab = page.locator('[role="tab"], button, span').filter(has_text=re.compile(ratio_pattern)).first
                if not ratio_tab.is_visible():
                    logging.debug("[Flow] Menu cấu hình bị đóng, đang mở lại để chọn tỷ lệ...")
                    config_btn.click(force=True)
                    page.wait_for_timeout(1000)
                ratio_tab.click(force=True)
                page.wait_for_timeout(500)
            except Exception as ex:
                logging.warning(f"[Flow] Không chọn được tỷ lệ {aspect_ratio}: {ex}")

        except Exception as e:
            logging.warning(f"[Flow] Bỏ qua bước thiết lập nâng cao: {e}")

        # 6. Click nút Tạo (Tạo / Generate / Create) - nút có icon arrow_forward
        try:
            logging.info("[Flow] Nhấn nút Tạo...")
            # Nút Tạo có cấu trúc: <button><i>arrow_forward</i><span>Tạo</span></button>
            failure_baseline = set(_generation_failed_tile_ids(page, visible_only=False))
            image_baseline = _generated_image_count(page)
            create_btn = page.locator('button:has(i:has-text("arrow_forward"))').first
            if not create_btn.is_visible():
                # Fallback: tim theo span text
                create_btn = page.locator('button:has(span:has-text("Tạo")), button:has(span:has-text("Generate")), button:has(span:has-text("Create"))').first
            create_btn.wait_for(state="visible", timeout=10000)
            create_btn.click(force=True)
            page.wait_for_timeout(2000)
            deadline = time.time() + 120
            while time.time() < deadline:
                if set(_generation_failed_tile_ids(page, visible_only=True)) - failure_baseline:
                    raise FlowGenerationFailed("Flow bao loi: Khong thanh cong")
                if _generated_image_count(page) > image_baseline:
                    break
                page.wait_for_timeout(1000)
        except FlowGenerationFailed:
            raise
        except Exception as e:
            logging.warning(f"[Flow] Lỗi khi click Tạo: {e}. Thử nhấn Enter...")
            page.keyboard.press("Control+Enter")
            if set(_generation_failed_tile_ids(page, visible_only=True)) - failure_baseline:
                raise FlowGenerationFailed("Flow bao loi: Khong thanh cong")

        # 7. Đợi quá trình sinh ảnh kết thúc
        logging.info("[Flow] Đang đợi ảnh được tạo hoàn thành...")
        # Ảnh tạo xong sẽ có thẻ <img alt="Hình ảnh được tạo"> hoặc <img alt="Generated image">
        generated_img = page.locator('img[alt="Hình ảnh được tạo"], img[alt="Generated image"]').last
        try:
            generated_img.wait_for(state="visible", timeout=120000)
            logging.info("[Flow] Ảnh đã được tạo xong!")
            page.wait_for_timeout(2000)  # Đợi thêm chút để UI ổn định
        except Exception as e:
            logging.warning(f"[Flow] Timeout chờ ảnh tạo xong: {e}. Thử tiếp tục tải...")
        
        # 8. Tải ảnh - Flow: Hover ảnh → Click ⋮ → Hover "Tải xuống" → Chọn chất lượng
        if set(_generation_failed_tile_ids(page, visible_only=True)) - failure_baseline:
            raise FlowGenerationFailed("Flow bao loi: Khong thanh cong")

        qualities = config.get("quality", ["1K"])
        target_quality = "1K"
        if "4K" in qualities:
            target_quality = "4K"
        elif "2K" in qualities:
            target_quality = "2K"
            
        file_path = _quality_file_path(save_path, final_name, target_quality)
        
        try:
            generated_img = page.locator('img[alt="Generated image"], img').last
            logging.debug("[Flow] Hover vao anh da tao de hien menu tai xuong...")
            downloaded = _download_quality(page, generated_img, target_quality, file_path)
            if not downloaded and target_quality != "1K":
                fallback_quality = "1K"
                fallback_path = _quality_file_path(save_path, final_name, fallback_quality)
                logging.warning(
                    f"[Flow] Khong the tai {target_quality}/upscale. Tu dong chuyen ver {fallback_quality}: {fallback_path}"
                )
                _download_quality(page, generated_img, fallback_quality, fallback_path)
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
            generated_img = page.locator('img[alt="Hình ảnh được tạo"], img[alt="Generated image"]').last
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
