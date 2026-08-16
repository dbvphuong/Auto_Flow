import logging
import re
import time


def _first_visible(locator):
    for index in range(locator.count()):
        candidate = locator.nth(index)
        try:
            if candidate.is_visible():
                return candidate
        except Exception:
            continue
    return None


def enter_flow_app(page, timeout=30000):
    """Enter the actual Flow application from its public marketing landing page."""
    cta_candidates = page.get_by_role(
        "button", name="Create with Google Flow", exact=True
    ).or_(page.get_by_role(
        "link", name="Create with Google Flow", exact=True
    ))
    cta = _first_visible(cta_candidates)
    if cta is None:
        # Flow hydrates this landing page asynchronously. Only wait for the CTA
        # while no dashboard/workspace marker is present, avoiding a delay in-app.
        landing_deadline = time.monotonic() + 10
        while time.monotonic() < landing_deadline:
            app_ready = _first_visible(page.locator(
                'button:has-text("Dự án mới"), button:has-text("New project"), '
                '[role="textbox"][contenteditable="true"], textarea:not([name*="recaptcha"])'
            ))
            if app_ready is not None or "accounts.google" in page.url:
                return False
            cta = _first_visible(cta_candidates)
            if cta is not None:
                break
            page.wait_for_timeout(250)
    if cta is None:
        return False

    logging.info("[Flow] Phát hiện trang giới thiệu; bấm 'Create with Google Flow'.")
    cta.click(force=True)
    try:
        page.wait_for_function(
            """() => {
                const text = document.body ? document.body.innerText : '';
                return location.hostname.includes('accounts.google')
                    || location.pathname.includes('/flow/project/')
                    || /Dự án mới|New project/i.test(text)
                    || !!document.querySelector('[role="textbox"][contenteditable="true"], textarea:not([name*="recaptcha"])');
            }""",
            timeout=timeout,
        )
    except Exception as exc:
        raise RuntimeError(
            "Đã bấm 'Create with Google Flow' nhưng ứng dụng không mở sau 30 giây."
        ) from exc
    return True


def dismiss_dashboard_promos(page):
    """Close dashboard banners/cards that can intercept the New project click."""
    dashboard = _first_visible(page.locator(
        'button:has-text("Dự án mới"), button:has-text("New project")'
    ))
    if dashboard is None:
        return 0

    closed = 0
    # On the dashboard Flow uses an icon-only `close` button for the large promo
    # card and the daily-credit banner; neither one is a dialog/modal.
    close_buttons = page.locator(
        'button:has(i.material-icons:text-is("close")), '
        'button:has(i.google-symbols:text-is("close"))'
    )
    for index in range(close_buttons.count() - 1, -1, -1):
        button = close_buttons.nth(index)
        try:
            if button.is_visible():
                button.click(force=True)
                closed += 1
                page.wait_for_timeout(250)
        except Exception:
            continue
    if closed:
        logging.info("[Flow] Đã đóng %s banner quảng cáo trên dashboard.", closed)
    return closed


def open_new_project(page, attempts=3):
    """Open a project, retrying dashboard UI clicks without submitting a prompt."""
    last_error = None
    for attempt in range(1, attempts + 1):
        dismiss_dashboard_promos(page)
        button = _first_visible(page.locator(
            'button:has-text("Dự án mới"), button:has-text("New project")'
        ))
        if button is None:
            if "/project/" in page.url:
                return
            last_error = RuntimeError("Không tìm thấy nút Dự án mới đang hiển thị.")
        else:
            try:
                logging.info("[Flow] Mở Dự án mới, lần %s/%s...", attempt, attempts)
                button.click(force=True)
                page.wait_for_function(
                    "() => location.pathname.includes('/project/') || location.search.includes('project=')",
                    timeout=15000,
                )
                logging.info("[Flow] Đã chuyển vào project mới.")
                return
            except Exception as exc:
                last_error = exc
        if attempt < attempts:
            page.wait_for_timeout(1000)
    raise RuntimeError(f"Flow không mở được dự án mới sau {attempts} lần: {last_error}")


def find_prompt_input(page, timeout=45000):
    """Return the visible Flow prompt editor and recover a stalled dashboard load."""
    candidates = page.locator(
        '[role="textbox"][contenteditable="true"], '
        'textarea:not([name*="recaptcha"]), '
        '[contenteditable="true"]:not([role="searchbox"])'
    )
    deadline = time.monotonic() + (timeout / 1000)
    dashboard_clicked = False
    project_reloaded = False
    started_at = time.monotonic()
    while time.monotonic() < deadline:
        prompt_input = _first_visible(candidates)
        if prompt_input is not None:
            return prompt_input

        # A dashboard click can be lost while Flow is still hydrating. Retrying it
        # here is safe because no prompt has been sent and no generation credit is used.
        if not dashboard_clicked and "/project/" not in page.url:
            new_project = _first_visible(page.locator(
                'button:has-text("Dự án mới"), button:has-text("New project")'
            ))
            if new_project is not None:
                logging.info("[Flow] Ô prompt chưa xuất hiện; thử mở Dự án mới thêm một lần.")
                new_project.click(force=True)
                dashboard_clicked = True

        # Occasionally the project URL changes but its workspace bundle never mounts.
        # Reload once before reporting an error; this is still before submit/generation.
        if (not project_reloaded and "/project/" in page.url
                and time.monotonic() - started_at >= 20):
            logging.warning("[Flow] Project đã mở nhưng chưa có ô prompt; tải lại workspace một lần.")
            page.reload(wait_until="domcontentloaded", timeout=60000)
            project_reloaded = True
        page.wait_for_timeout(250)
    raise RuntimeError(
        f"Không tìm thấy ô nhập prompt của Google Flow sau {timeout // 1000} giây; URL={page.url}"
    )


def detect_flow_layout(page):
    """Detect the direct-generation layout or the new agent-side-panel layout."""
    settings_buttons = page.locator(
        'button:has-text("tune"), '
        'button[aria-label*="agent settings" i], '
        'button[aria-label*="cài đặt tác nhân" i]'
    )
    session_text = page.get_by_text(
        re.compile(r"Phiên không có tiêu đề|Untitled session|Cài đặt tác nhân|Agent settings", re.I)
    )
    if (_first_visible(settings_buttons) is not None
            or _first_visible(session_text) is not None):
        return "agent"

    old_config = page.locator(
        'button:has-text("Nano Banana"), button:has-text("Imagen"), '
        'button:has-text("Veo"), button:has-text("Omni")'
    )
    if _first_visible(old_config) is not None:
        return "classic"

    raise RuntimeError(
        "Khong nhan dien duoc giao dien Google Flow (cu/moi). "
        "Hay cap nhat HTML/selector cua giao dien hien tai."
    )


def fill_prompt(prompt_input, prompt):
    prompt_input.wait_for(state="visible", timeout=15000)
    prompt_input.click(force=True)
    prompt_input.fill(prompt)


def _prompt_panel(prompt_input):
    panel = prompt_input.locator(
        'xpath=ancestor::*[.//button[contains(., "arrow_forward") '
        'or contains(., "send") or contains(., "Tạo") or contains(., "Create")]][1]'
    )
    return panel if panel.count() else prompt_input.locator("xpath=ancestor::div[1]")


def click_generate(page, prompt_input):
    """Click the submit button tied to the active prompt instead of using shortcuts."""
    panel = _prompt_panel(prompt_input)
    create_btn = _first_visible(panel.locator(
        'button:has(i.google-symbols:has-text("arrow_forward")), '
        'button:has(i.material-icons:has-text("arrow_forward")), '
        'button:has(i:has-text("arrow_forward"))'
    ))
    if create_btn is None:
        create_btn = _first_visible(panel.locator(
            'button:has(i.google-symbols:has-text("send")), '
            'button:has(i.material-icons:has-text("send")), '
            'button:has(i:has-text("send"))'
        ))
    if create_btn is None:
        raise RuntimeError("Khong tim thay nut gui prompt cua Google Flow.")
    if create_btn.is_disabled():
        raise RuntimeError("Nut gui prompt cua Google Flow dang bi vo hieu hoa.")
    create_btn.click(force=True)


def generation_is_busy(page):
    """Return True while Flow has accepted a request but has not exposed progress yet.

    The agent layout keeps the submitted text in the conversation and replaces the
    send arrow with a square ``stop`` button.  During its initial "Interpreting the
    Instruction" phase there may be no tile and no percentage for quite a while.
    """
    try:
        return bool(page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el || !(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) return false;
                    let node = el;
                    while (node && node !== document.body) {
                        const style = getComputedStyle(node);
                        const opacity = Number.parseFloat(style.opacity);
                        if (style.display === 'none' || style.visibility === 'hidden'
                                || (!Number.isNaN(opacity) && opacity < 0.1)) return false;
                        node = node.parentElement;
                    }
                    return true;
                };
                const stopButton = Array.from(document.querySelectorAll('button')).some(button => {
                    if (!visible(button)) return false;
                    const icons = Array.from(button.querySelectorAll('i'))
                        .map(icon => (icon.textContent || '').trim().toLowerCase());
                    return icons.includes('stop') || icons.includes('cancel');
                });
                if (stopButton) return true;

                const busyText = /^(interpreting the instruction|interpreting|dang dien giai|đang diễn giải|đang xử lý|processing)$/i;
                return Array.from(document.querySelectorAll('button, div, span, p')).some(el =>
                    visible(el) && busyText.test((el.textContent || '').trim()));
            }"""
        ))
    except Exception:
        return False


def _classic_config_button(page):
    candidates = page.locator(
        'button:has-text("Nano Banana"), button:has-text("Imagen"), '
        'button:has-text("Veo"), button:has-text("Omni"), '
        'button:has-text("Flash"), button:has-text("Quality")'
    )
    button = _first_visible(candidates)
    if button is None:
        raise RuntimeError("Khong tim thay nut cau hinh cua giao dien Flow cu.")
    return button


def _visible_menu(page):
    menus = page.locator('[role="menu"], [data-radix-menu-content], .DropdownMenuContent')
    menu = _first_visible(menus)
    if menu is None:
        raise RuntimeError("Bang cau hinh Flow cu khong mo.")
    return menu


def _click_tab(root, text):
    pattern = re.compile(rf"^\s*(?:\S+\s+)?{re.escape(text)}\s*$", re.I)
    candidates = root.locator('button[role="tab"], [role="tab"]').filter(has_text=pattern)
    tab = _first_visible(candidates)
    if tab is None:
        candidates = root.locator('button').filter(has_text=re.compile(re.escape(text), re.I))
        tab = _first_visible(candidates)
    if tab is None:
        raise RuntimeError(f"Khong tim thay tuy chon '{text}' trong cau hinh Flow.")
    if tab.get_attribute("aria-selected") != "true":
        tab.click(force=True)


def _select_model(page, root, model_name):
    model_buttons = root.locator('button[aria-haspopup="menu"]')
    trigger = _first_visible(model_buttons)
    if trigger is None:
        raise RuntimeError("Khong tim thay danh sach model cua Flow.")
    current_text = trigger.inner_text()
    if model_name.lower() in current_text.lower():
        return
    trigger.click(force=True)
    page.wait_for_timeout(300)
    option = page.locator('[role="menuitem"], [role="option"]').filter(
        has_text=re.compile(re.escape(model_name), re.I)
    )
    item = _first_visible(option)
    if item is None:
        raise RuntimeError(f"Khong tim thay model '{model_name}' tren Google Flow.")
    try:
        item.click(force=True)
    except Exception:
        item.evaluate("element => element.click()")


def configure_classic_image(page, config):
    config_btn = _classic_config_button(page)
    config_btn.click(force=True)
    page.wait_for_timeout(300)
    menu = _visible_menu(page)
    _click_tab(menu, "Hình ảnh" if menu.get_by_text("Hình ảnh", exact=True).count() else "Image")

    model = _image_model_value(config.get("model", "Nano Banana 2"))
    _select_model(page, menu, model)
    _click_tab(menu, _ratio_value(config.get("aspect_ratio", "16:9")))
    _click_tab(menu, f"x{int(config.get('images_per_prompt', 1))}")


def configure_classic_video(page, config):
    config_btn = _classic_config_button(page)
    config_btn.click(force=True)
    page.wait_for_timeout(300)
    menu = _visible_menu(page)
    _click_tab(menu, "Video")

    selected_model = config.get("model", "Veo 3.1 - Fast [20 Credit]")
    keyword = "Fast"
    if "Lite" in selected_model:
        keyword = "Lite"
    elif "Flash" in selected_model or "Omni" in selected_model:
        keyword = "Omni"
    elif "Quality" in selected_model:
        keyword = "Quality"
    _select_model(page, menu, keyword)
    _click_tab(menu, _ratio_value(config.get("aspect_ratio", "16:9")))
    _click_tab(menu, "x1")


def _ratio_value(value):
    for ratio in ("16:9", "9:16", "1:1", "4:3", "3:4"):
        if ratio in str(value):
            return ratio
    return "16:9"


def _image_model_value(value):
    value = str(value)
    if "Imagen 4" in value:
        return "Imagen 4"
    if "Nano Banana Pro" in value:
        return "Nano Banana Pro"
    return "Nano Banana 2"


def _agent_settings_panel(page, prompt_input):
    existing = page.get_by_text(
        re.compile(r"^\s*(Cài đặt tác nhân|Agent settings)\s*$", re.I)
    ).first
    if not existing.is_visible():
        prompt_panel = _prompt_panel(prompt_input)
        settings_btn = _first_visible(prompt_panel.locator(
            'button:has-text("tune"), '
            'button[aria-label*="agent settings" i], '
            'button[aria-label*="cài đặt tác nhân" i]'
        ))
        if settings_btn is None:
            raise RuntimeError("Khong tim thay nut mo Cai dat tac nhan cua Flow moi.")
        settings_btn.click(force=True)
        existing.wait_for(state="visible", timeout=10000)

    panel = existing.locator(
        'xpath=ancestor::*[.//button[contains(., "Lưu") or contains(., "Save")]][1]'
    )
    if not panel.count():
        raise RuntimeError("Khong xac dinh duoc panel Cai dat tac nhan cua Flow moi.")
    return panel


def _agent_section_heading(panel, media_type):
    if media_type == "image":
        pattern = re.compile(r"(?:mặc định.*tạo hình ảnh|default.*image generation)", re.I)
    else:
        pattern = re.compile(r"(?:mặc định.*tạo video|default.*video generation)", re.I)
    heading = panel.get_by_text(pattern).first
    if not heading.is_visible():
        raise RuntimeError(f"Khong tim thay muc cau hinh {media_type} cua Flow moi.")
    return heading


def _click_following_tab(heading, text):
    tab = heading.locator(
        f'xpath=following::button[@role="tab" and contains(normalize-space(.), "{text}")][1]'
    )
    tab.wait_for(state="visible", timeout=5000)
    for _ in range(3):
        if tab.get_attribute("aria-selected") == "true":
            return
        tab.click(force=True)
        tab.page.wait_for_timeout(400)
    raise RuntimeError(f"Flow moi khong ghi nhan tuy chon '{text}'.")


def _select_following_model(page, heading, model_names, requested):
    conditions = " or ".join(f'contains(., "{name}")' for name in model_names)
    trigger = heading.locator(f'xpath=following::button[{conditions}][1]')
    trigger.wait_for(state="visible", timeout=5000)
    if requested.lower() in trigger.inner_text().lower():
        return
    trigger.click(force=True)
    page.wait_for_timeout(300)
    option = page.locator('[role="menuitem"], [role="option"]').filter(
        has_text=re.compile(re.escape(requested), re.I)
    )
    item = _first_visible(option)
    if item is None:
        raise RuntimeError(f"Khong tim thay model '{requested}' trong Flow moi.")
    try:
        item.click(force=True)
    except Exception:
        item.evaluate("element => element.click()")


def configure_agent_defaults(page, prompt_input, media_type, config):
    """Configure the default generation controls in the new agent panel."""
    panel = _agent_settings_panel(page, prompt_input)
    # Panel mới tải cấu hình đã lưu bất đồng bộ. Nếu click quá sớm, dữ liệu từ
    # server sẽ ghi đè lựa chọn x1 vừa đặt thành x2 cũ.
    page.wait_for_timeout(2000)

    never_ask = panel.locator(
        'button[role="radio"][value="AUTO_APPROVE"]'
    ).first
    if never_ask.is_visible() and never_ask.get_attribute("aria-checked") != "true":
        never_ask.click(force=True)
        page.wait_for_timeout(400)
        if never_ask.get_attribute("aria-checked") != "true":
            raise RuntimeError("Flow moi khong ghi nhan che do Khong bao gio hoi.")

    heading = _agent_section_heading(panel, media_type)
    _click_following_tab(heading, _ratio_value(config.get("aspect_ratio", "16:9")))

    if media_type == "image":
        _click_following_tab(heading, f"x{int(config.get('images_per_prompt', 1))}")
        model = _image_model_value(config.get("model", "Nano Banana 2"))
        _select_following_model(page, heading, ("Nano", "Banana", "Imagen"), model)
    else:
        _click_following_tab(heading, "x1")
        selected = config.get("model", "Veo 3.1 - Fast [20 Credit]")
        if "Omni" in selected or "Flash" in selected:
            model = "Omni Flash"
        elif "Lite" in selected:
            model = "Veo 3.1 - Lite"
        elif "Quality" in selected:
            model = "Veo 3.1 - Quality"
        else:
            model = "Veo 3.1 - Fast"
        _select_following_model(page, heading, ("Veo", "Omni", "Flash"), model)

    save = _first_visible(panel.locator('button').filter(
        has_text=re.compile(r"^\s*(Lưu|Save)\s*$", re.I)
    ))
    if save is None:
        raise RuntimeError("Khong tim thay nut Luu trong Cai dat tac nhan.")
    save.click(force=True)
    try:
        panel.wait_for(state="hidden", timeout=15000)
    except Exception as exc:
        raise RuntimeError("Panel Cai dat tac nhan khong dong sau khi Luu.") from exc
    logging.info("[Flow] Da luu cau hinh cho giao dien tac nhan moi.")


def configure_generation(page, prompt_input, media_type, config):
    layout = detect_flow_layout(page)
    logging.info("[Flow] Nhan dien giao dien: %s", "moi" if layout == "agent" else "cu")
    if layout == "agent":
        configure_agent_defaults(page, prompt_input, media_type, config)
    elif media_type == "image":
        configure_classic_image(page, config)
    else:
        configure_classic_video(page, config)
    return layout
