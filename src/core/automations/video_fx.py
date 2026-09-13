import os
import time
import logging
import re
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from common.diagnostics import save_page_diagnostics
from .flow_ui import (
    click_generate, configure_generation, dismiss_dashboard_promos,
    enter_flow_app, find_prompt_input, generation_is_busy, open_new_project,
)

class FlowGenerationFailed(Exception):
    """A Flow generation failure with an explicit queue retry decision."""

    def __init__(self, message, *, retryable=True):
        super().__init__(message)
        self.retryable = retryable


class FlowCleanupFailed(Exception):
    pass


class FlowQualityUnavailable(Exception):
    pass


_POLICY_REJECTION_MARKERS = (
    "vi phạm chính sách",
    "người nổi tiếng",
    "nội dung gây hại",
    "nội dung tình dục",
    "may violate our polic",
    "might violate our polic",
    "could violate our polic",
    "violates our polic",
    "policy violation",
    "public figure",
    "celebrity",
    "harmful content",
    "sexual content",
    "safety filter",
    "blocked for safety",
)

_PRE_PROGRESS_PROMPT_REJECTION_MARKERS = (
    "không tải được video",
    "couldn't generate video",
    "could not generate video",
    "unable to generate video",
)


def _terminal_generation_error_retryable(error_text, progress_seen):
    """Retry every confirmed Flow failure except a policy rejection.

    Flow can return its generic no-charge error after the percentage has
    started moving. That is still a transient server-side failure and should
    be requeued. Replaying the same prompt is only suppressed when Flow says
    the prompt violates a content/safety policy.
    """
    normalized = (error_text or "").casefold()
    if any(marker in normalized for marker in _POLICY_REJECTION_MARKERS):
        return False
    # Current Flow often reduces an invalid prompt to the generic card
    # "Không thành công / Không tải được video". It is a prompt rejection only
    # when no real percentage has ever appeared. After progress it is transient.
    if not progress_seen and any(
        marker in normalized for marker in _PRE_PROGRESS_PROMPT_REJECTION_MARKERS
    ):
        return False
    return True

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

def _ensure_desktop_download_layout(page, minimum_width=1280):
    """Keep Flow's tile actions available inside narrow, tiled Chrome windows.

    Five visible workers make each native Chrome window roughly 512 px wide.
    Flow then switches to its compact layout, where the tile-local three-dot
    menu is no longer the same desktop menu used for quality downloads.  A CDP
    metrics override changes only the page's CSS viewport; the native Chrome
    window remains in its assigned screen slot.
    """
    if getattr(page, "_auto_flow_desktop_download_layout", False):
        return False

    try:
        viewport = page.evaluate(
            "() => ({width: window.innerWidth, height: window.innerHeight})"
        )
        width = int(viewport.get("width") or 0)
        if width >= minimum_width:
            page._auto_flow_desktop_download_layout = True
            return False

        height = max(720, int(viewport.get("height") or 0))
        cdp_session = page.context.new_cdp_session(page)
        cdp_session.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": minimum_width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        # Retain the session for the lifetime of the page. Some Chromium
        # versions clear emulation overrides when the CDP session is collected.
        page._auto_flow_download_cdp_session = cdp_session
        page._auto_flow_desktop_download_layout = True
        page.wait_for_timeout(500)
        logging.info(
            "[Flow Video] Chuyển viewport tải từ %spx sang %spx; "
            "cửa sổ Chrome ngoài màn hình vẫn giữ nguyên ô.",
            width, minimum_width,
        )
        return True
    except Exception as exc:
        # Continue with the current layout so non-Chromium/mocked pages retain
        # the previous behaviour.
        logging.warning(
            "[Flow Video] Không chuyển được viewport tải sang desktop: %s", exc
        )
        return False


def _open_download_menu(page, generated_tile):
    video_tile_key = None
    try:
        media_src = generated_tile.locator(
            'video, img[alt*="video" i], img[alt*="Video" i]'
        ).first.get_attribute("src") or ""
        if media_src:
            video_tile_key = page.evaluate(
                "src => new URL(src, location.href).pathname", media_src
            )
    except Exception:
        pass

    layout_changed = _ensure_desktop_download_layout(page)
    if layout_changed:
        # Responsive reflow can replace the tile DOM. Reacquire it by ID when
        # possible instead of operating on a stale locator/element handle.
        try:
            tile_id = generated_tile.get_attribute("data-tile-id")
            if tile_id:
                generated_tile = page.locator(
                    f'[data-tile-id="{tile_id}"]'
                ).filter(has=page.locator("video, canvas")).first
                generated_tile.wait_for(state="attached", timeout=5000)
            elif video_tile_key:
                generated_tile = _find_generated_tile(
                    page, f"video-key:{video_tile_key}", timeout_ms=10000
                )
        except Exception:
            pass

    more_selector = (
        'button:has(mat-icon:text-is("more_vert")), '
        'button:has(i:has-text("more_vert")), button:has-text("more_vert"), '
        '[aria-label*="more" i], [aria-label*="khác" i], '
        '[aria-label*="tùy chọn" i]'
    )
    more_btn = None
    for hover_attempt in range(1, 4):
        logging.info(
            "[Flow Video] Hover tile để hiện nút 3 chấm, lần %s/3...",
            hover_attempt,
        )
        try:
            generated_tile.scroll_into_view_if_needed(timeout=5000)
            generated_tile.hover(force=True, timeout=5000)
            page.wait_for_timeout(500)
        except Exception as exc:
            logging.warning(
                "[Flow Video] Hover tile lần %s/3 lỗi: %s", hover_attempt, exc
            )
            continue

        candidates = generated_tile.locator(more_selector)
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            try:
                if candidate.is_visible():
                    more_btn = candidate
                    break
            except Exception:
                continue
        if more_btn is not None:
            break

    if more_btn is None:
        raise RuntimeError(
            "Khong tim thay nut 3 cham dang hien thi trong dung tile video sau 3 lan hover."
        )

    logging.info("[Flow Video] Bấm nút 3 chấm của đúng tile video...")
    more_btn.click(force=True, timeout=5000)
    page.wait_for_timeout(500)

    # Mở submenu chất lượng. Flow dùng menu lồng nhau: phải hover/click mục
    # "Tải xuống" trước thì 720p/1080p/4K mới xuất hiện.
    download_candidates = page.locator(
        '[role="menuitem"]:has(mat-icon:text-is("download")), '
        '[role="menuitem"]:has(i:has-text("download")), '
        '[data-radix-menu-content] [role="menuitem"]:has-text("Tải xuống"), '
        '[data-radix-menu-content] [role="menuitem"]:has-text("Download"), '
        '.DropdownMenuContent [role="menuitem"]:has-text("Tải xuống"), '
        '.DropdownMenuContent [role="menuitem"]:has-text("Download"), '
        '[role="menu"] [role="menuitem"]:has-text("Tải xuống"), '
        '[role="menu"] [role="menuitem"]:has-text("Download"), '
        '[role="menuitem"][aria-haspopup="menu"]:has-text("Tải xuống"), '
        '[role="menuitem"][aria-haspopup="menu"]:has-text("Download")'
    )
    download_menu = None
    for index in range(download_candidates.count()):
        candidate = download_candidates.nth(index)
        try:
            if candidate.is_visible():
                download_menu = candidate
                break
        except Exception:
            continue
    if download_menu is None:
        download_menu = download_candidates.first
        download_menu.wait_for(state="visible", timeout=10000)

    logging.info("[Flow Video] Đã mở menu 3 chấm; hover mục Tải xuống...")
    download_menu.hover(force=True, timeout=5000)
    try:
        download_menu.focus()
    except Exception:
        pass
    page.wait_for_timeout(800)
    return download_menu


def _delete_generated_video(page, generated_tile, tile_id=None):
    """Delete the generated Flow tile after its local download is verified."""
    try:
        generated_tile.hover()
        page.wait_for_timeout(500)
        more_btn = _first_visible_in_tile = None
        candidates = generated_tile.locator(
            'button:has(i:has-text("more_vert")), '
            'button:has-text("more_vert"), [aria-label*="more" i], '
            '[aria-label*="khác" i], [aria-label*="tùy chọn" i]'
        )
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            if candidate.is_visible():
                _first_visible_in_tile = candidate
                break
        more_btn = _first_visible_in_tile
        if more_btn is None:
            raise RuntimeError("Khong tim thay nut tuy chon cua tile video.")
        more_btn.click(force=True)
        page.wait_for_timeout(500)

        delete_pattern = re.compile(
            r"(?:Xóa|Delete)(?:\s+video)?\s*$|"
            r"(?:Chuyển vào thùng rác|Move to trash)",
            re.I,
        )
        delete_item = None
        candidates = page.locator('[role="menuitem"], [role="option"], button').filter(
            has_text=delete_pattern
        )
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            if candidate.is_visible():
                delete_item = candidate
                break
        if delete_item is None:
            raise RuntimeError("Khong tim thay tuy chon Xoa video trong menu tile.")
        try:
            delete_item.click(force=True)
        except Exception:
            delete_item.evaluate("element => element.click()")
        page.wait_for_timeout(500)

        dialogs = page.locator('[role="dialog"], [aria-modal="true"]')
        for index in range(dialogs.count()):
            dialog = dialogs.nth(index)
            if not dialog.is_visible():
                continue
            confirm = dialog.locator('button').filter(has_text=delete_pattern)
            for button_index in range(confirm.count()):
                button = confirm.nth(button_index)
                if button.is_visible():
                    try:
                        button.click(force=True)
                    except Exception:
                        button.evaluate("element => element.click()")
                    break
        if tile_id:
            page.wait_for_function(
                "tileId => !Array.from(document.querySelectorAll('[data-tile-id]'))"
                ".some(tile => tile.getAttribute('data-tile-id') === tileId)",
                arg=tile_id,
                timeout=15000,
            )
        else:
            generated_tile.wait_for(state="detached", timeout=15000)
        logging.info("[Flow Video] Da xoa video tren Google Flow sau khi tai xong.")
        return True
    except Exception as exc:
        logging.error("[Flow Video] Khong xoa duoc video sau khi tai: %s", exc)
        return False

def _classify_upscale_status(text):
    """Return failure/success/progress for Flow's visible upscale messages."""
    normalized = " ".join(str(text or "").lower().split())
    failure_markers = (
        "không tăng độ phân giải được",
        "không thể tăng độ phân giải",
        "tăng độ phân giải video thất bại",
        "tăng độ phân giải không thành công",
        "unable to upscale",
        "can't upscale",
        "cannot upscale",
        "failed to upscale",
        "upscale failed",
    )
    success_markers = (
        "đã tăng độ phân giải video",
        "tăng độ phân giải video thành công",
        "video upscaled successfully",
        "upscale complete",
    )
    progress_markers = (
        "đang tăng độ phân giải video",
        "upscaling video",
        "upscale in progress",
    )
    if any(marker in normalized for marker in failure_markers):
        return "failure"
    if any(marker in normalized for marker in success_markers):
        return "success"
    if any(marker in normalized for marker in progress_markers):
        return "progress"
    return None


def _visible_upscale_status(page):
    """Read only visible toast/status text, avoiding stale hidden page text."""
    candidates = page.locator(
        '[data-sonner-toast], [role="alert"], [role="status"], '
        'li:has-text("tăng độ phân giải"), li:has-text("upscal")'
    )
    for index in range(candidates.count()):
        candidate = candidates.nth(index)
        try:
            if not candidate.is_visible():
                continue
            text = candidate.inner_text().strip()
            state = _classify_upscale_status(text)
            if state is None and candidate.locator(
                'i:has-text("error"), [data-icon="error"]'
            ).count() > 0:
                state = "failure"
            if state:
                return state, text
        except Exception:
            continue
    return None, ""


def _quality_menu_item(page, quality):
    return page.locator(
        f'[data-radix-menu-content] [role="menuitem"]:has-text("{quality}"), '
        f'.DropdownMenuContent [role="menuitem"]:has-text("{quality}"), '
        f'[role="menu"] [role="menuitem"]:has-text("{quality}"), '
        f'[role="menuitem"]:has-text("{quality}")'
    ).last


def _open_quality_item(page, generated_tile, quality):
    download_menu = _open_download_menu(page, generated_tile)
    quality_btn = _quality_menu_item(page, quality)
    try:
        quality_btn.wait_for(state="visible", timeout=5000)
    except Exception:
        download_menu.click(force=True)
        quality_btn.wait_for(state="visible", timeout=5000)
    logging.info("[Flow Video] Đã mở danh sách chất lượng; chọn %s.", quality)
    label = quality_btn.inner_text().strip().lower()
    if (quality_btn.get_attribute("aria-disabled") == "true"
            or quality_btn.get_attribute("data-disabled") is not None):
        raise FlowQualityUnavailable(
            f"Tùy chọn {quality} đang bị Flow khóa: "
            f"{label.replace(chr(10), ' | ')}"
        )
    return quality_btn, label


def _download_ready_quality(
    page, generated_tile, quality, file_path, downloads,
    initial_button=None, attempts=3,
):
    """Click an already-ready quality, reopening the menu after a missed click."""
    quality_btn = initial_button
    for attempt in range(1, attempts + 1):
        if quality_btn is None:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            page.wait_for_timeout(700)
            quality_btn, _ = _open_quality_item(
                page, generated_tile, quality
            )

        quality_btn.click(force=True)
        logging.info(
            "[Flow Video] Đã bấm tải %s, chờ sự kiện download (lần %s/%s)...",
            quality, attempt, attempts,
        )
        download_deadline = time.time() + 30
        while time.time() < download_deadline:
            if downloads:
                downloads[0].save_as(file_path)
                logging.info(
                    "[Flow Video] Trình duyệt đã phát sự kiện download %s.", quality
                )
                return True
            page.wait_for_timeout(500)

        if attempt < attempts:
            logging.warning(
                "[Flow Video] %s đã sẵn sàng nhưng click tải không phản hồi; "
                "thử lại %s/%s.",
                quality, attempt, attempts - 1,
            )
            quality_btn = None

    raise PlaywrightTimeoutError(
        f"{quality} đã sẵn sàng nhưng không phát sinh download sau "
        f"{attempts} lần bấm."
    )


def _download_video(page, generated_tile, quality, file_path):

    # Đăng ký bộ lắng nghe download TRƯỚC khi thao tác mở menu
    downloads = []
    download_handler = lambda download: downloads.append(download)
    page.on("download", download_handler)

    try:
        logging.debug(f"[Flow Video] Chọn chất lượng tải xuống: {quality}")
        quality_btn, quality_label = _open_quality_item(
            page, generated_tile, quality
        )
        ready_markers = ("đã tăng", "upscaled", "upscale complete")

        # 720p, or a 1080p item that was already upscaled, downloads directly.
        if quality.lower() == "720p" or any(
            marker in quality_label for marker in ready_markers
        ):
            return _download_ready_quality(
                page, generated_tile, quality, file_path, downloads,
                initial_button=quality_btn,
            )

        # Each account handles its own upscale independently. A visible failure
        # retries the 1080 action up to three times. No terminal response within
        # three minutes returns control to the configured 720p fallback.
        max_upscale_retries = 3
        max_upscale_attempts = 1 + max_upscale_retries
        last_failure = None
        for upscale_attempt in range(1, max_upscale_attempts + 1):
            attempt_failure = None
            if upscale_attempt > 1:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                _close_visible_toasts(page)
                page.wait_for_timeout(1000)
                quality_btn, quality_label = _open_quality_item(
                    page, generated_tile, quality
                )
                if any(marker in quality_label for marker in ready_markers):
                    return _download_ready_quality(
                        page, generated_tile, quality, file_path, downloads,
                        initial_button=quality_btn,
                    )

            logging.info(
                "[Flow Video] Bấm upscale %s lượt %s/%s; "
                "chờ trạng thái tối đa 3 phút.",
                quality, upscale_attempt, max_upscale_attempts,
            )
            quality_btn.click(force=True)
            deadline = time.time() + 180
            next_menu_check = time.time() + 5
            last_status = None
            ignore_old_failure_until = time.time() + 2

            while time.time() < deadline:
                if downloads:
                    downloads[0].save_as(file_path)
                    return True

                state, status_text = _visible_upscale_status(page)
                status_key = (state, status_text)
                if state and status_key != last_status:
                    logging.info(
                        "[Flow Video] Trạng thái upscale %s: %s",
                        quality, status_text.replace("\n", " | "),
                    )
                    last_status = status_key
                if state == "failure" and time.time() >= ignore_old_failure_until:
                    attempt_failure = RuntimeError(
                        f"Flow báo upscale {quality} thất bại lần "
                        f"{upscale_attempt}/{max_upscale_attempts}: {status_text}"
                    )
                    break

                if time.time() >= next_menu_check:
                    next_menu_check = time.time() + 5
                    try:
                        page.keyboard.press("Escape")
                        quality_btn, new_label = _open_quality_item(
                            page, generated_tile, quality
                        )
                        if any(marker in new_label for marker in ready_markers):
                            logging.info(
                                "[Flow Video] %s đã tăng độ phân giải thành công; tải file.",
                                quality,
                            )
                            return _download_ready_quality(
                                page, generated_tile, quality, file_path,
                                downloads, initial_button=quality_btn,
                            )
                        page.keyboard.press("Escape")
                    except FlowQualityUnavailable:
                        raise
                    except PlaywrightTimeoutError:
                        raise
                    except Exception as poll_error:
                        logging.debug(
                            "[Flow Video] Chưa đọc được menu trạng thái %s: %s",
                            quality, poll_error,
                        )
                page.wait_for_timeout(500)

            if attempt_failure is None:
                raise PlaywrightTimeoutError(
                    f"Sau 3 phút Flow chưa trả kết quả upscale {quality}; "
                    "chuyển sang 720p."
                )
            last_failure = attempt_failure
            if upscale_attempt < max_upscale_attempts:
                logging.warning(
                    "[Flow Video] %s; retry %s/%s.",
                    last_failure, upscale_attempt, max_upscale_retries,
                )

        raise last_failure or RuntimeError(
            f"Flow upscale {quality} thất bại sau "
            f"{max_upscale_retries} lần retry."
        )
    finally:
        try:
            page.remove_listener("download", download_handler)
        except Exception:
            pass


def _find_generated_tile(page, tile_id, fallback_tile=None, timeout_ms=60000):
    """Reacquire the generated tile after a page reload using its stable ID."""
    if not tile_id:
        if fallback_tile is None:
            raise RuntimeError("Không có tile_id để tìm lại video sau khi F5.")
        return fallback_tile

    if tile_id.startswith("video-key:"):
        video_key = tile_id.removeprefix("video-key:")
        deadline = time.monotonic() + (timeout_ms / 1000)
        while time.monotonic() < deadline:
            media_items = page.locator(
                'video, img[alt*="video" i], img[alt*="Video" i]'
            )
            for index in range(media_items.count()):
                media = media_items.nth(index)
                src = media.get_attribute("src") or ""
                try:
                    key = page.evaluate(
                        "src => new URL(src, location.href).pathname", src
                    )
                except Exception:
                    key = src.split("?", 1)[0]
                if key != video_key:
                    continue
                tile = media.locator("xpath=ancestor::flow-grid-tile-container[1]")
                if not tile.count():
                    tile = media.locator("xpath=ancestor::flow-tile-container[1]")
                return tile if tile.count() else media.locator("xpath=ancestor::div[1]")
            page.wait_for_timeout(500)
        if fallback_tile is not None:
            return fallback_tile
        raise RuntimeError("Khong tim lai duoc video theo source key sau khi F5.")

    tiles = page.locator(f'[data-tile-id="{tile_id}"]')
    tiles.first.wait_for(state="attached", timeout=timeout_ms)
    for index in range(tiles.count()):
        candidate = tiles.nth(index)
        try:
            if candidate.locator("video, canvas").count() > 0:
                return candidate
            if candidate.locator(
                'button:has-text("more_vert"), [aria-label*="more" i]'
            ).count() > 0:
                return candidate
        except Exception:
            continue
    return tiles.first


def _reload_generated_tile(page, tile_id, generated_tile):
    logging.info("[Flow Video] F5 trang trước lần tải lại; tile_id=%s", tile_id)
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    page.reload(wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2500)
    _close_welcome_popups(page)
    tile = _find_generated_tile(page, tile_id, generated_tile)
    try:
        tile.locator("video, canvas").first.wait_for(state="attached", timeout=60000)
    except Exception:
        # The tile menu is sufficient for download even when the video element
        # is lazy-mounted outside the viewport.
        tile.locator(
            'button:has-text("more_vert"), [aria-label*="more" i]'
        ).first.wait_for(state="attached", timeout=15000)
    return tile


def _download_video_with_retry(
    page, generated_tile, quality, file_path, attempts=3,
    tile_id=None, reload_between_attempts=False,
):
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
                if reload_between_attempts:
                    try:
                        generated_tile = _reload_generated_tile(
                            page, tile_id, generated_tile
                        )
                    except Exception as reload_error:
                        logging.warning(
                            "[Flow Video] F5/tìm lại tile gặp lỗi trước lần %s: %s",
                            attempt + 1, reload_error,
                        )
                else:
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
                    page.wait_for_timeout(1500)
    raise last_error


def _download_configured_quality(
    page, generated_tile, tile_id, target_quality, save_path, final_name,
):
    """Run the target-quality state machine, then one 720p fallback.

    Returns (downloaded, path, current_tile, error). When both menu paths fail,
    the returned path is deliberately the 720p path so the authenticated source
    fallback cannot mislabel an original-resolution file as 1080p/4K.
    """
    target_path = _quality_file_path(save_path, final_name, target_quality)
    try:
        _download_video_with_retry(
            page, generated_tile, target_quality, target_path,
            attempts=1, tile_id=tile_id, reload_between_attempts=False,
        )
        try:
            generated_tile = _find_generated_tile(
                page, tile_id, generated_tile, timeout_ms=10000
            )
        except Exception:
            pass
        return True, target_path, generated_tile, None
    except Exception as quality_error:
        if target_quality == "720p":
            return False, target_path, generated_tile, quality_error

        fallback_quality = "720p"
        fallback_path = _quality_file_path(save_path, final_name, fallback_quality)
        logging.warning(
            "[Flow Video] Không tải được %s (%s). Chuyển sang tải %s.",
            target_quality, quality_error, fallback_quality,
        )
        try:
            generated_tile = _find_generated_tile(
                page, tile_id, generated_tile
            )
        except Exception:
            pass
        try:
            _download_video_with_retry(
                page, generated_tile, fallback_quality, fallback_path,
                attempts=1, tile_id=tile_id,
            )
            return True, fallback_path, generated_tile, None
        except Exception as fallback_error:
            return False, fallback_path, generated_tile, fallback_error


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
            r"""(body) => {
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


def _visible_failure_tile_records(page):
    """Return visible Flow error cards, including mobile tiles without IDs."""
    try:
        return page.locator("body").evaluate(
            r"""(body) => {
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
                return Array.from(body.querySelectorAll('flow-error-tile'))
                    .filter(visible)
                    .map((errorTile, index) => {
                        const tile = errorTile.closest(
                            'flow-grid-tile-container, flow-tile-container, [data-tile-id]'
                        ) || errorTile;
                        const text = (errorTile.innerText || errorTile.textContent || '')
                            .replace(/\s+/g, ' ').trim();
                        const label = (tile.getAttribute('aria-label') || '').trim();
                        const tileId = (tile.getAttribute('data-tile-id') || '').trim();
                        return {
                            key: tileId || `${label}\n${text}` || `visible-error-${index}`,
                            text,
                        };
                    });
            }"""
        ) or []
    except Exception:
        return []


def _new_visible_failure(records, baseline_keys):
    """Select only an error card created by the current submission."""
    for record in records:
        if record.get("key") not in baseline_keys and record.get("text"):
            return record
    return None


def _visible_terminal_generation_error(page, tile_id=None):
    """Return an explicit no-charge error from the exact generated tile."""
    try:
        return page.locator("body").evaluate(
            """(body, tileId) => {
                const titlePattern = /Không thành công|Unsuccessful/i;
                const detailPattern = /Rất tiếc, đã xảy ra lỗi|Bạn chưa bị tính phí|Không tải được video|Đã hết thời gian tạo|Sorry[^.]*error|not be charged|won't be charged|couldn.t generate video|unable to generate video|generation timed out/i;
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
                const roots = tileId
                    ? Array.from(body.querySelectorAll('[data-tile-id]')).filter(
                        el => el.getAttribute('data-tile-id') === tileId && visible(el))
                    : [body];
                const icons = roots.flatMap(root => Array.from(root.querySelectorAll('i, mat-icon'))).reverse();
                for (const icon of icons) {
                    const iconName = (icon.textContent || '').trim().toLowerCase();
                    if (!['warning', 'warning_amber', 'error'].includes(iconName)
                            || !visible(icon)) continue;
                    // Flow has at least two rollouts: the failure tile can be
                    // wrapped by a button, or directly by span > div[data-tile-id].
                    const card = icon.closest(
                        '[data-tile-id], flow-grid-tile-container, flow-tile-container, flow-error-tile'
                    ) || icon.closest('button') || icon.parentElement;
                    if (!card || !visible(card)) continue;
                    if (tileId && card.getAttribute('data-tile-id') !== tileId) continue;
                    const text = (card.innerText || card.textContent || '').trim();
                    if (titlePattern.test(text) && detailPattern.test(text)) return text;
                }
                return '';
            }""",
            tile_id,
        ) or ""
    except Exception:
        return ""


def _generation_queue_message_visible(page):
    """Return True for Agent messages that say the request is still queued."""
    try:
        return page.locator("body").evaluate(
            """(body) => {
                const pattern = /scheduled|waiting in the queue|currently in the queue|high demand|đang xếp hàng|đang chờ xử lý/i;
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


def _is_generation_failure_candidate(
    *, progress, seconds_since_progress, tile_failed,
    agent_error, queued, progress_seen, terminal_error=False,
):
    """Apply debounce/scope rules before counting a terminal generation error."""
    # Flow can briefly mount duplicate DOM nodes with the same data-tile-id.
    # A live percentage on any visible copy always wins over a failure card.
    if progress is not None:
        return False
    if terminal_error:
        return True
    if queued or seconds_since_progress < 30:
        return False
    if tile_failed:
        return True
    # A global Agent error is only useful before this job has ever shown real
    # progress. Once a tile has progressed, unrelated/stale Agent cards must not
    # terminate it.
    return bool(agent_error and not progress_seen)


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


def _generated_video_keys(page):
    """Return stable media paths for completed videos in Flow's ID-less tile UI."""
    try:
        return set(page.locator(
            'video, img[alt*="video" i], img[alt*="Video" i]'
        ).evaluate_all(
            """items => items.map(item => {
                const src = item.src || item.querySelector?.('source')?.src || '';
                if (!src) return '';
                try { return new URL(src, location.href).pathname; }
                catch (_) { return src.split('?')[0]; }
            }).filter(Boolean)"""
        ))
    except Exception:
        return set()

def run_video_fx(context, account, prompt, task_id, config):
    # Mỗi lần gọi chỉ được gửi đúng một request. Giữ nguyên exception (đặc biệt
    # thuộc tính retryable) để worker không vô tình requeue lỗi vĩnh viễn.
    try:
        return _run_video_fx_once(context, account, prompt, task_id, config)
    except Exception as exc:
        logging.error("[Flow Video] Tạo video thất bại: %s", exc)
        raise

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
            existing_video_keys = _generated_video_keys(page)
            existing_failure_keys = {
                record["key"] for record in _visible_failure_tile_records(page)
            }
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
                    new_video_keys = _generated_video_keys(page) - existing_video_keys
                    if new_video_keys:
                        new_tile_id = f"video-key:{next(iter(new_video_keys))}"
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
        pre_tile_failure_key = None
        pre_tile_failure_streak = 0
        while new_tile_id is None and time.time() < generation_deadline:
            current_tiles = set(page.locator('[data-tile-id]').evaluate_all(
                'elements => elements.map(el => el.getAttribute("data-tile-id"))'
            ))
            new_tiles = current_tiles - existing_tiles
            if new_tiles:
                new_tile_id = list(new_tiles)[0]
                logging.info(f"[Flow Video] Đã phát hiện tile mới: {new_tile_id}")
                break
            new_video_keys = _generated_video_keys(page) - existing_video_keys
            if new_video_keys:
                new_tile_id = f"video-key:{next(iter(new_video_keys))}"
                logging.info(
                    "[Flow Video] Đã phát hiện video mới trong giao diện tile không ID: %s",
                    new_tile_id,
                )
                break
            # New mobile Flow error cards have no data-tile-id and no media src.
            # Check only a card created after this submit. A visible percentage
            # always wins, preventing a mounted error card from killing live work.
            progress = _generation_progress(page)
            if progress is not None:
                pre_tile_failure_key = None
                pre_tile_failure_streak = 0
            else:
                failure = _new_visible_failure(
                    _visible_failure_tile_records(page), existing_failure_keys
                )
                if failure:
                    if failure["key"] == pre_tile_failure_key:
                        pre_tile_failure_streak += 1
                    else:
                        pre_tile_failure_key = failure["key"]
                        pre_tile_failure_streak = 1
                    if pre_tile_failure_streak == 1:
                        logging.warning(
                            "[Flow Video] Phát hiện card lỗi mới chưa có tile ID; "
                            "chờ xác nhận 3 lần liên tiếp."
                        )
                    if pre_tile_failure_streak >= 3:
                        error_text = failure["text"]
                        retryable = _terminal_generation_error_retryable(
                            error_text, progress_seen=False
                        )
                        raise FlowGenerationFailed(
                            f"Google Labs Flow từ chối/tạo video không thành công: {error_text}",
                            retryable=retryable,
                        )
                else:
                    pre_tile_failure_key = None
                    pre_tile_failure_streak = 0
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
        last_progress_at = time.time()
        progress_seen = False
        failure_streak = 0
        terminal_error_streak = 0
        terminal_error_text = ""
        while time.time() < generation_deadline:
            progress = _generation_progress(page, new_tile_id)
            if progress is not None:
                last_progress_at = time.time()
                progress_seen = True
                failure_streak = 0
                terminal_error_streak = 0
                terminal_error_text = ""
                if progress != last_progress:
                    logging.info("[Flow Video] Video đang được tạo: %s%%; tiếp tục chờ...", progress)
                    last_progress = progress
            
            # Kiểm tra xem video đã sẵn sàng chưa trong tile (đã có video src)
            is_ready = page.evaluate(
                """(tileId) => {
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
                    let tiles;
                    if (tileId && tileId.startsWith('video-key:')) {
                        const key = tileId.slice('video-key:'.length);
                        tiles = Array.from(document.querySelectorAll(
                            'video, img[alt*="video" i]'
                        ))
                            .filter(media => {
                                try { return new URL(media.src, location.href).pathname === key; }
                                catch (_) { return (media.src || '').split('?')[0] === key; }
                            })
                            .map(media => media.closest(
                                'flow-grid-tile-container, flow-tile-container'
                            ) || media.parentElement)
                            .filter(visible);
                    } else if (tileId) {
                        tiles = Array.from(document.querySelectorAll('[data-tile-id]')).filter(
                            tile => tile.getAttribute('data-tile-id') === tileId && visible(tile));
                    } else {
                        tiles = [document.body];
                    }
                    return tiles.some(tile => {
                        const video = tile.querySelector('video');
                        let hasVideo = false;
                        if (video) {
                            const videoSrc = video.src || '';
                            const source = video.querySelector('source');
                            const sourceSrc = source ? source.src : '';
                            hasVideo = (videoSrc.length > 0) || (sourceSrc.length > 0);
                        }
                        if (!hasVideo) {
                            hasVideo = !!tile.querySelector('img[alt*="video" i]');
                        }
                        const text = (tile.textContent || "").toLowerCase();
                        const isGenerating = text.includes("%") || text.includes("đang tạo")
                            || text.includes("generating") || text.includes("chuẩn bị")
                            || text.includes("preparing");
                        return hasVideo && !isGenerating;
                    });
                }""",
                new_tile_id
            )
            if is_ready:
                video_ready = True
                logging.info(f"[Flow Video] Video trong tile {new_tile_id} đã sẵn sàng!")
                break

            if progress is None:
                current_terminal_error = _visible_terminal_generation_error(
                    page, new_tile_id
                )
                if current_terminal_error:
                    if current_terminal_error == terminal_error_text:
                        terminal_error_streak += 1
                    else:
                        terminal_error_text = current_terminal_error
                        terminal_error_streak = 1
                    if terminal_error_streak == 1:
                        logging.warning(
                            "[Flow Video] Tile %s vừa đổi sang card lỗi; "
                            "chờ xác nhận 3 lần liên tiếp.",
                            new_tile_id,
                        )
                    if terminal_error_streak >= 3:
                        retryable = _terminal_generation_error_retryable(
                            terminal_error_text, progress_seen
                        )
                        logging.error(
                            "[Flow Video] Flow xác nhận card lỗi tạo video "
                            "(retryable=%s): %s",
                            retryable,
                            terminal_error_text.replace("\n", " | "),
                        )
                        raise FlowGenerationFailed(
                            f"Google Labs Flow báo lỗi tạo video trên tile "
                            f"{new_tile_id or 'chưa gán ID'}: "
                            f"{terminal_error_text.replace(chr(10), ' | ')}",
                            retryable=retryable,
                        )
                else:
                    terminal_error_streak = 0
                    terminal_error_text = ""

                queued = _generation_queue_message_visible(page)
                failed_ids = _generation_failed_tile_ids(page, visible_only=True)
                tile_failed = bool(new_tile_id and new_tile_id in failed_ids)
                failure_candidate = _is_generation_failure_candidate(
                    progress=progress,
                    seconds_since_progress=time.time() - last_progress_at,
                    tile_failed=tile_failed,
                    agent_error=_visible_agent_error(page),
                    queued=queued,
                    progress_seen=progress_seen,
                    terminal_error=False,
                )
                if failure_candidate:
                    failure_streak += 1
                    if failure_streak == 1:
                        logging.warning(
                            "[Flow Video] Phát hiện dấu hiệu lỗi trên tile %s; "
                            "chờ xác nhận 3 lần liên tiếp.",
                            new_tile_id,
                        )
                else:
                    failure_streak = 0
                if failure_streak >= 3:
                    raise FlowGenerationFailed(
                        f"Google Labs Flow báo lỗi tạo video trên tile {new_tile_id or 'chưa gán ID'}.",
                        retryable=True,
                    )
                
            page.wait_for_timeout(2000)
            
        if not video_ready:
            if _generation_progress(page, new_tile_id) is not None:
                raise RuntimeError(
                    "Flow vẫn đang tạo video sau 10 phút; không tự retry để tránh trừ credit/tạo trùng."
                )
            raise FlowGenerationFailed(f"Flow không hoàn tất video sau 10 phút trên tile {new_tile_id}.")

        # 7. Đặt tile container kết quả chính xác bằng ID của tile mới (tránh trùng lặp ID gây lỗi strict mode)
        if new_tile_id and new_tile_id.startswith("video-key:"):
            generated_tiles = _find_generated_tile(page, new_tile_id)
        else:
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

        # 8. Tải video. Trạng thái ready ở trên đã được xác nhận bằng video src;
        # không kiểm tra lại failure card vì Flow giữ card cũ trong DOM.

        # Xác định chất lượng video
        qualities = config.get("quality", ["720p"])
        target_quality = "720p"
        if "4K" in qualities:
            target_quality = "4K"
        elif "1080p" in qualities:
            target_quality = "1080p"

        try:
            downloaded, file_path, generated_tile, menu_error = (
                _download_configured_quality(
                    page, generated_tile, new_tile_id, target_quality,
                    save_path, final_name,
                )
            )
            if not downloaded:
                raise menu_error
            
            logging.info(f"[Flow Video] Đã lưu video thành công vào: {file_path}")
            _clean_error_files(save_path, final_name)
            try:
                from core.browser_manager import update_account_credits_and_type_from_page
                update_account_credits_and_type_from_page(page, account.id)
            except Exception as cu_err:
                logging.warning(f"[Flow Video] Không thể cập nhật credits: {cu_err}")
            if config.get("delete_after_download"):
                if not _delete_generated_video(page, generated_tile, new_tile_id):
                    raise FlowCleanupFailed(
                        "Video da tai xong nhung khong xoa duoc tren Google Flow."
                    )
            page.close()
            return file_path
        except FlowCleanupFailed:
            raise
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
                
                pointer = session.get(src_url, timeout=120)
                pointer.raise_for_status()
                if len(pointer.content) < 1024:
                    raise RuntimeError(
                        "URL video trả về nội dung rỗng hoặc không hợp lệ."
                    )
                with open(file_path, "wb") as f:
                    f.write(pointer.content)
                logging.info(f"[Flow Video] Đã lưu video qua src thành công: {file_path}")
                _clean_error_files(save_path, final_name)
                try:
                    from core.browser_manager import update_account_credits_and_type_from_page
                    update_account_credits_and_type_from_page(page, account.id)
                except Exception as cu_err:
                    logging.warning(f"[Flow Video] Không thể cập nhật credits: {cu_err}")
                if config.get("delete_after_download"):
                    if not _delete_generated_video(page, generated_tile, new_tile_id):
                        raise FlowCleanupFailed(
                            "Video da tai xong nhung khong xoa duoc tren Google Flow."
                        )
                page.close()
                return file_path
            else:
                raise Exception("Không tìm thấy thuộc tính src trên thẻ video/source kết quả.")
        except FlowCleanupFailed:
            raise
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
            save_page_diagnostics(page, "flow_video_fail", final_name)
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
            save_page_diagnostics(page, "flow_video_error", final_name)
        except Exception as se:
            if "Tài khoản đã bị đăng xuất" in str(se):
                raise
        page.close()
        raise Exception(f"Lỗi kịch bản Flow Video: {str(e)}")
