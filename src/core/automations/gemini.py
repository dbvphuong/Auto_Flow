import os
import re
import time
import logging

from common.gemini_languages import LANGUAGE_BY_COUNTRY


COMPLETED_PARTS_PATTERN = re.compile(
    r"Đã[ \t]+hoàn[ \t]+thành[ \t]+(?P<part_count>\d+)"
    r"[ \t]*/[ \t]*(?P=part_count)[ \t]+part\b",
    re.IGNORECASE,
)


class GeminiStopped(Exception):
    """Raised when the owning worker asks the automation to stop."""


class GeminiProUnavailable(Exception):
    """Raised when the current account cannot switch to the required Pro model."""


def _safe_file_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "gemini_story")
    return value.strip(" .") or "gemini_story"


def _completion_marker(text, done_marker):
    """Return the marker that proves Gemini completed all requested parts."""
    if done_marker:
        marker_match = re.search(re.escape(done_marker), text, re.IGNORECASE)
        if marker_match:
            return marker_match.group(0)
    match = COMPLETED_PARTS_PATTERN.search(text)
    return match.group(0) if match else None


def _remove_completion_markers(text, done_marker):
    """Remove control markers from the story saved for the user."""
    if done_marker:
        text = re.sub(re.escape(done_marker), "", text, flags=re.IGNORECASE)
    return COMPLETED_PARTS_PATTERN.sub("", text)


def _wait_for_control(config):
    is_stopped = config.get("is_stopped", lambda: False)
    is_paused = config.get("is_paused", lambda: False)
    paused_logged = False
    while is_paused():
        if not paused_logged:
            logging.info("[Gemini Web] Automation đang tạm dừng, chờ tín hiệu tiếp tục...")
            paused_logged = True
        if is_stopped():
            raise GeminiStopped("Đã dừng theo yêu cầu")
        time.sleep(0.25)
    if paused_logged:
        logging.info("[Gemini Web] Đã nhận tín hiệu tiếp tục")
    if is_stopped():
        raise GeminiStopped("Đã dừng theo yêu cầu")


def _wait_for_response(page, response_index, config):
    timeout_seconds = max(30, int(config.get("response_timeout", 600)))
    deadline = time.time() + timeout_seconds
    selector = "model-response message-content .markdown"
    last_text = ""
    stable_since = None
    detected = False
    started_at = time.time()
    logging.info(
        "[Gemini Web] Chờ phản hồi index=%s, timeout=%ss, selector=%s",
        response_index, timeout_seconds, selector,
    )

    while time.time() < deadline:
        _wait_for_control(config)
        responses = page.locator(selector)
        if responses.count() > response_index:
            response = responses.nth(response_index)
            text = response.inner_text(timeout=3000).strip()
            is_busy = response.get_attribute("aria-busy") == "true"
            if not detected:
                detected = True
                logging.info(
                    "[Gemini Web] Đã phát hiện response index=%s sau %.1fs",
                    response_index, time.time() - started_at,
                )
            if text and text == last_text and not is_busy:
                stable_since = stable_since or time.time()
                if time.time() - stable_since >= 2:
                    logging.info(
                        "[Gemini Web] Response index=%s hoàn tất sau %.1fs; ký tự=%s; aria-busy=%s",
                        response_index, time.time() - started_at, len(text), is_busy,
                    )
                    return text
            else:
                stable_since = None
                last_text = text
        time.sleep(0.5)

    logging.error(
        "[Gemini Web] Timeout response index=%s sau %.1fs; detected=%s; ký tự cuối=%s",
        response_index, time.time() - started_at, detected, len(last_text),
    )
    raise TimeoutError(f"Gemini không hoàn tất phản hồi sau {timeout_seconds} giây")


def _prompt_box_text(prompt_box):
    """Return visible/editor text so a failed Enter press can be detected."""
    return (prompt_box.evaluate(
        "element => (element.innerText || element.textContent || '').trim()"
    ) or "").strip()


def _submit_prompt(page, prompt_box, message, config, stage):
    """Press Enter and verify that Gemini actually consumed the editor content."""
    max_attempts = max(1, int(config.get("submit_attempts", 3)))
    verify_seconds = max(1.0, float(config.get("submit_verify_seconds", 5)))
    prompt_box.fill(message)

    for attempt in range(1, max_attempts + 1):
        _wait_for_control(config)
        prompt_box.click()
        prompt_box.press("Enter")
        deadline = time.time() + verify_seconds
        remaining_text = message

        while time.time() < deadline:
            _wait_for_control(config)
            remaining_text = _prompt_box_text(prompt_box)
            if not remaining_text:
                logging.info(
                    "[Gemini Web] [%s] Web đã nhận nội dung sau lần Enter %s/%s",
                    stage, attempt, max_attempts,
                )
                return
            time.sleep(0.25)

        logging.warning(
            "[Gemini Web] [%s] Enter lần %s/%s chưa gửi được; "
            "ô nhập vẫn còn %s ký tự: %r",
            stage, attempt, max_attempts, len(remaining_text), remaining_text[:80],
        )

    remaining_text = _prompt_box_text(prompt_box)
    raise RuntimeError(
        f"{stage}: đã nhấn Enter {max_attempts} lần nhưng web chưa nhận gửi; "
        f"ô nhập vẫn còn {len(remaining_text)} ký tự"
    )


def _send_and_collect(page, message, config, response_timeout=None, stage="Không xác định"):
    _wait_for_control(config)
    response_selector = "model-response message-content .markdown"
    response_index = page.locator(response_selector).count()
    prompt_box = page.locator('.ql-editor[contenteditable="true"][role="textbox"]').first
    logging.info(
        "[Gemini Web] [%s] Chuẩn bị gửi; ký tự=%s; response hiện có=%s; timeout=%s",
        stage, len(message), response_index, response_timeout or config.get("response_timeout", 600),
    )
    prompt_box.wait_for(state="visible", timeout=60000)
    _submit_prompt(page, prompt_box, message, config, stage)
    wait_config = config
    if response_timeout is not None:
        wait_config = config.copy()
        wait_config["response_timeout"] = response_timeout
    return _wait_for_response(page, response_index, wait_config)


def _ensure_pro_model(page):
    """Open the model menu and switch only when 3.1 Pro is not selected."""
    try:
        model_button = page.locator('[data-test-id="bard-mode-menu-button"]')
        model_button.wait_for(state="visible", timeout=60000)
        current_label = model_button.get_attribute("aria-label") or ""
        logging.info("[Gemini Web] Nút model hiện tại: aria-label=%r", current_label)
        model_button.click()
        pro_item = page.locator('[role="menuitem"]').filter(has_text="3.1 Pro")
        pro_item.wait_for(state="visible", timeout=15000)
        item_classes = pro_item.get_attribute("class") or ""
        is_selected = "selected" in item_classes.split()
        logging.info(
            "[Gemini Web] Kiểm tra mục 3.1 Pro: selected=%s; class=%r",
            is_selected, item_classes,
        )
        if is_selected:
            page.keyboard.press("Escape")
            logging.info("[Gemini Web] Model đã là 3.1 Pro, không đổi model")
            return

        if pro_item.get_attribute("aria-disabled") == "true":
            raise GeminiProUnavailable("Mục 3.1 Pro đang bị vô hiệu hóa")

        pro_item.click(timeout=10000)
        page.wait_for_timeout(1000)

        # Mở lại menu để xác nhận thao tác chuyển model thực sự thành công.
        model_button.click()
        pro_item = page.locator('[role="menuitem"]').filter(has_text="3.1 Pro")
        pro_item.wait_for(state="visible", timeout=15000)
        verified_classes = pro_item.get_attribute("class") or ""
        verified = "selected" in verified_classes.split()
        page.keyboard.press("Escape")
        if not verified:
            raise GeminiProUnavailable("Gemini không chuyển sang model 3.1 Pro")
        logging.info("[Gemini Web] Đã chọn và xác nhận model 3.1 Pro")
    except GeminiProUnavailable:
        raise
    except Exception as exc:
        raise GeminiProUnavailable(
            "Không thể chọn model 3.1 Pro; tài khoản có thể đã hết token Pro"
        ) from exc


def run_gemini(context, account, prompt, task_id, config):
    """Keep asking Gemini to continue until the configured completion marker appears."""
    page = context.pages[0] if context.pages else context.new_page()
    logging.info(
        "[Gemini Web][Batch %s] Bắt đầu; country=%s; max_gõ_1=%s; marker=%r",
        task_id, config.get("target_country"), config.get("max_continuations", 10),
        config.get("done_marker", "[[DONE]]"),
    )
    logging.info("[Gemini Web][Batch %s] Điều hướng https://gemini.google.com/app", task_id)
    page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=90000)
    logging.info(
        "[Gemini Web][Batch %s] Trang đã tải DOM; url=%s; title=%r",
        task_id, page.url, page.title(),
    )

    prompt_box = page.locator('.ql-editor[contenteditable="true"][role="textbox"]').first
    try:
        prompt_box.wait_for(state="visible", timeout=60000)
    except Exception as exc:
        if "accounts.google.com" in page.url:
            raise RuntimeError("Tài khoản đã bị đăng xuất khỏi Gemini") from exc
        raise RuntimeError("Không tìm thấy ô nhập prompt Gemini") from exc

    _ensure_pro_model(page)

    max_continuations = max(1, int(config.get("max_continuations", 10)))
    done_marker = (config.get("done_marker") or "[[DONE]]").strip()
    target_country = (config.get("target_country") or "").strip()
    target_language = LANGUAGE_BY_COUNTRY.get(target_country, target_country)
    master_prompt = (config.get("master_prompt") or "").strip()
    language_replaced = False
    if target_country and master_prompt:
        master_prompt, replacement_count = re.subn(
            r"\[ngôn ngữ\]", target_language, master_prompt, flags=re.IGNORECASE
        )
        language_replaced = replacement_count > 0
        logging.info(
            "[Gemini Web][Batch %s] Replace [Ngôn ngữ]: country=%s; language=%r; số_lần=%s",
            task_id, target_country, target_language, replacement_count,
        )
    if target_country and not language_replaced:
        master_prompt += (
            f"\n\nNGÔN NGỮ MỤC TIÊU: {target_language}"
            f"\nQUỐC GIA MỤC TIÊU: {target_country}"
        )
        logging.warning(
            "[Gemini Web][Batch %s] Master Prompt không có [Ngôn ngữ]; đã thêm quốc gia mục tiêu cuối prompt",
            task_id,
        )
    part_callback = config.get("part_callback", lambda current, total: None)
    story_parts = []

    # Phản hồi Master Prompt chỉ dùng thiết lập phiên, không ghi vào file truyện.
    if master_prompt:
        master_response = _send_and_collect(
            page, master_prompt, config, stage="Master Prompt"
        )
        logging.info(
            "[Gemini Web][Batch %s] Đã nhận phản hồi Master Prompt; ký tự=%s (không lưu vào truyện)",
            task_id, len(master_response),
        )

    # Nội dung truyện bắt đầu từ phản hồi sau khi gửi cốt truyện.
    story_parts.append(_send_and_collect(
        page, prompt.strip(), config, stage="Cốt truyện / Hook-Part 1"
    ))
    combined_text = "\n\n".join(story_parts)
    completion_marker = _completion_marker(combined_text, done_marker)
    logging.info(
        "[Gemini Web][Batch %s] Nội dung đầu: phần=%s; tổng ký tự=%s; marker_found=%s",
        task_id, len(story_parts), len(combined_text), bool(completion_marker),
    )
    if not completion_marker:
        for continuation_number in range(1, max_continuations + 1):
            logging.info(
                "[Gemini Web][Batch %s] Gửi '1' lần %s/%s",
                task_id, continuation_number, max_continuations,
            )
            try:
                response = _send_and_collect(
                    page, "1", config, response_timeout=300,
                    stage=f"Gõ 1 lần {continuation_number}/{max_continuations}",
                )
            except TimeoutError as exc:
                raise RuntimeError(
                    f"Lần gửi '1' thứ {continuation_number}: Gemini không phản hồi trong 5 phút"
                ) from exc
            story_parts.append(response)
            part_callback(continuation_number, max_continuations)
            combined_text = "\n\n".join(story_parts)
            completion_marker = _completion_marker(combined_text, done_marker)
            logging.info(
                "[Gemini Web][Batch %s] Sau lần %s: response ký tự=%s; tổng phần=%s; tổng ký tự=%s; marker_found=%s",
                task_id, continuation_number, len(response), len(story_parts),
                len(combined_text), bool(completion_marker),
            )
            if completion_marker:
                logging.info(
                    "[Gemini Web][Batch %s] Gặp marker %r sau %s lần gõ '1'",
                    task_id, completion_marker, continuation_number,
                )
                break
        else:
            logging.error(
                "[Gemini Web][Batch %s] Hết giới hạn %s lần nhưng không gặp marker %r",
                task_id, max_continuations, done_marker,
            )
            raise RuntimeError(
                f"Đã gửi '1' đủ {max_continuations} lần nhưng không tìm thấy {done_marker}"
            )

    output_dir = os.path.abspath(config["output_dir"])
    os.makedirs(output_dir, exist_ok=True)
    file_name = _safe_file_name(config.get("batch_name")) + ".txt"
    result_path = os.path.join(output_dir, file_name)
    temp_path = result_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8-sig", newline="\n") as output_file:
        # Marker chỉ dùng điều khiển vòng lặp, không ghi vào nội dung truyện cuối.
        final_text = _remove_completion_markers(
            "\n\n".join(story_parts), done_marker
        ).strip()
        output_file.write(final_text + "\n")
    os.replace(temp_path, result_path)
    logging.info(
        "[Gemini Web][Batch %s] Lưu thành công: path=%s; phần=%s; ký tự=%s",
        task_id, result_path, len(story_parts), len(final_text),
    )
    return result_path
