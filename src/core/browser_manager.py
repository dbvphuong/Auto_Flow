import json
from playwright.sync_api import sync_playwright
import os
import re
import logging
import threading
import time


# ── Theo dõi các tiến trình Chrome do bot mở ──────────────────────────────────
_active_chrome_pids = set()
_active_pids_lock = threading.Lock()


def _is_local_port_open(port):
    """Kiểm tra cổng CDP mà không phụ thuộc vào Playwright."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
            return True
    except (OSError, TypeError, ValueError):
        return False


def _tail_text_file(path, max_chars=6000):
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - max_chars * 4), os.SEEK_SET)
            return stream.read().decode("utf-8", errors="replace")[-max_chars:]
    except Exception as exc:
        return f"<không đọc được log Chrome: {exc}>"


def get_browser_runtime_diagnostics(context, include_log_tail=True):
    """Lấy trạng thái tiến trình Chrome kể cả khi Playwright đã ngừng."""
    runtime = getattr(context, "_auto_flow_chrome_runtime", None)
    if not runtime:
        return {"available": False}

    proc = runtime["process"]
    exit_code = proc.poll()
    diagnostics = {
        "available": True,
        "pid": proc.pid,
        "alive": exit_code is None,
        "exit_code": exit_code,
        "exit_code_hex": f"0x{exit_code & 0xFFFFFFFF:08X}" if exit_code is not None else None,
        "uptime_seconds": round(time.monotonic() - runtime["started_at"], 1),
        "cdp_port": runtime["port"],
        "cdp_port_open": _is_local_port_open(runtime["port"]),
        "close_requested": runtime["close_requested"],
        "user_data_dir": runtime["user_data_dir"],
        "chrome_log": runtime["log_path"],
    }
    if include_log_tail:
        diagnostics["chrome_log_tail"] = _tail_text_file(runtime["log_path"])
    return diagnostics


def log_browser_runtime_diagnostics(context, label):
    diagnostics = get_browser_runtime_diagnostics(context, include_log_tail=True)
    log_tail = diagnostics.pop("chrome_log_tail", "")
    logging.error("[Browser Diagnostics][%s] %s", label, diagnostics)
    if log_tail.strip():
        logging.error("[Browser Diagnostics][%s] Chrome stderr tail:\n%s", label, log_tail.rstrip())
    else:
        logging.error("[Browser Diagnostics][%s] Chrome stderr không có nội dung", label)
    return diagnostics

def register_chrome_pid(pid):
    with _active_pids_lock:
        _active_chrome_pids.add(pid)
        logging.info(f"[Browser] Đã đăng ký Chrome PID {pid}. Danh sách hiện tại: {list(_active_chrome_pids)}")

def unregister_chrome_pid(pid):
    with _active_pids_lock:
        _active_chrome_pids.discard(pid)
        logging.info(f"[Browser] Đã hủy đăng ký Chrome PID {pid}. Danh sách hiện tại: {list(_active_chrome_pids)}")

def kill_all_registered_chromes():
    with _active_pids_lock:
        pids = list(_active_chrome_pids)
    
    import subprocess
    logging.info(f"[Browser] Bắt đầu kill tất cả Chrome đã đăng ký: {pids}")
    for pid in pids:
        try:
            subprocess.run(f'taskkill /F /PID {pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logging.info(f"[Browser] Đã force-kill Chrome PID {pid}")
        except Exception as e:
            logging.warning(f"[Browser] Không thể force-kill Chrome PID {pid}: {e}")
            
    with _active_pids_lock:
        _active_chrome_pids.clear()


def get_descendant_pids(parent_pid):
    """
    Quét danh sách tiến trình con và cháu của parent_pid thông qua wmic.
    """
    descendants = {parent_pid}
    try:
        import subprocess
        cmd = 'wmic process where "name=\'chrome.exe\'" get ParentProcessId, ProcessId /format:csv'
        output = subprocess.check_output(cmd, shell=True, text=True, errors='ignore')
        
        parent_to_children = {}
        for line in output.splitlines():
            line = line.strip()
            if not line or "Node,ParentProcessId,ProcessId" in line:
                continue
            parts = line.split(',')
            if len(parts) >= 3:
                try:
                    p_pid = int(parts[1].strip())
                    c_pid = int(parts[2].strip())
                    parent_to_children.setdefault(p_pid, []).append(c_pid)
                except ValueError:
                    pass
                    
        queue = [parent_pid]
        while queue:
            curr = queue.pop(0)
            if curr in parent_to_children:
                for child in parent_to_children[curr]:
                    if child not in descendants:
                        descendants.add(child)
                        queue.append(child)
    except Exception as e:
        logging.warning(f"[Browser] Lỗi quét cây tiến trình con: {e}")
    return descendants


def _minimize_chrome_window(pid, prev_active_hwnd=None):
    """
    Tìm cửa sổ Chrome theo PID (hoặc các tiến trình con của nó) và thu nhỏ (minimize) để chạy gọn gàng ở nền.
    Chạy trong thread riêng để không block luồng chính.
    """
    import time
    try:
        import win32gui
        import win32process
        import win32con
    except ImportError:
        logging.debug("[Browser] pywin32 chưa cài, bỏ qua thu nhỏ cửa sổ.")
        return

    # Chrome thường khởi động và tự động đẩy cửa sổ lên Foreground sau vài giây.
    # Do đó, cần liên tục kiểm tra và minimize trong khoảng 10 giây đầu tiên.
    deadline = time.time() + 10
    last_pids_update = 0
    last_enum_time = 0
    descendant_pids = set()
    minimized_any = False
    
    start_time = time.time()
    while time.time() < deadline:
        current_time = time.time()
        elapsed = current_time - start_time
        
        # Cập nhật danh sách PID con mỗi 2 giây
        if current_time - last_pids_update > 2.0:
            descendant_pids = get_descendant_pids(pid)
            last_pids_update = current_time
            
        # Kiểm tra và khôi phục focus cực nhanh nếu Chrome cướp focus
        if prev_active_hwnd:
            try:
                curr_foreground = win32gui.GetForegroundWindow()
                if curr_foreground and curr_foreground != prev_active_hwnd:
                    _, fg_pid = win32process.GetWindowThreadProcessId(curr_foreground)
                    if fg_pid in descendant_pids:
                        if win32gui.IsWindow(prev_active_hwnd):
                            win32gui.SetForegroundWindow(prev_active_hwnd)
                            win32gui.ShowWindow(curr_foreground, win32con.SW_MINIMIZE)
                            logging.info(f"[Browser] Đã trả lại focus cực nhanh cho cửa sổ cũ: {prev_active_hwnd}")
            except Exception:
                pass

        # Quét và minimize toàn bộ cửa sổ Chrome định kỳ mỗi 200ms
        if current_time - last_enum_time > 0.2:
            last_enum_time = current_time
            found = []
            def _enum_cb(h, _):
                try:
                    if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h):
                        _, wpid = win32process.GetWindowThreadProcessId(h)
                        if wpid in descendant_pids:
                            found.append(h)
                except Exception:
                    pass
            win32gui.EnumWindows(_enum_cb, None)
            
            main = [h for h in found if win32gui.GetWindowText(h)]
            for hwnd in main:
                try:
                    tup = win32gui.GetWindowPlacement(hwnd)
                    if tup[1] != win32con.SW_SHOWMINIMIZED:
                        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                        minimized_any = True
                except Exception:
                    pass

        # Trong 3 giây đầu tiên khi Chrome đang khởi động, kiểm tra liên tục mỗi 15ms.
        # Sau đó giãn cách ra kiểm tra mỗi 100ms.
        if elapsed < 3.0:
            time.sleep(0.015)
        else:
            time.sleep(0.1)

    if minimized_any:
        logging.info(f"[Browser] Chrome PID {pid} (hoặc tiến trình con) đã được liên tục thu nhỏ (minimize) thành công.")
    else:
        logging.debug(f"[Browser] Không tìm được HWND để minimize cho PID {pid} sau 10 giây.")


def get_primary_work_area():
    """Return the usable primary-screen rectangle as (left, top, right, bottom)."""
    if os.name != "nt":
        return 0, 0, 1920, 1080
    try:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception as exc:
        logging.warning("[Browser] Không đọc được work area: %s", exc)
    try:
        import ctypes
        return 0, 0, ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    except Exception:
        return 0, 0, 1920, 1080


def calculate_tiled_window_bounds(slot_index, slot_count, work_area=None):
    """Split the work area into equal horizontal columns and return x, y, width, height."""
    count = max(1, int(slot_count))
    index = min(max(0, int(slot_index)), count - 1)
    left, top, right, bottom = work_area or get_primary_work_area()
    total_width = max(1, right - left)
    height = max(1, bottom - top)
    x = left + (total_width * index // count)
    next_x = left + (total_width * (index + 1) // count)
    return x, top, max(1, next_x - x), height


def _tile_chrome_window(pid, bounds):
    """Keep a newly launched Chrome window in its assigned screen column."""
    import time
    try:
        import win32con
        import win32gui
        import win32process
    except ImportError:
        logging.warning("[Browser] Thiếu pywin32, chỉ áp dụng --window-position/--window-size")
        return

    x, y, width, height = bounds
    deadline = time.time() + 10
    descendant_pids = set()
    last_pids_update = 0
    positioned = set()
    while time.time() < deadline:
        now = time.time()
        if now - last_pids_update > 1:
            descendant_pids = get_descendant_pids(pid)
            last_pids_update = now

        found = []
        def _enum_cb(hwnd, _):
            try:
                if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
                    _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
                    if window_pid in descendant_pids:
                        found.append(hwnd)
            except Exception:
                pass

        win32gui.EnumWindows(_enum_cb, None)
        for hwnd in found:
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
                win32gui.SetWindowPos(
                    hwnd, win32con.HWND_NOTOPMOST, x, y, width, height,
                    win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
                )
                positioned.add(hwnd)
            except Exception as exc:
                logging.debug("[Browser] Chưa đặt được HWND %s: %s", hwnd, exc)
        time.sleep(0.1 if positioned else 0.05)

    if positioned:
        logging.info(
            "[Browser] Đã tile Chrome PID %s vào x=%s y=%s width=%s height=%s; hwnd=%s",
            pid, x, y, width, height, list(positioned),
        )
    else:
        logging.warning("[Browser] Không tìm thấy cửa sổ để tile cho Chrome PID %s", pid)


def get_profile_dir(email):
    # Dọn dẹp email để tạo tên thư mục hợp lệ và an toàn
    clean_email = re.sub(r'[^a-zA-Z0-9@._-]', '_', email)
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(src_dir)
    profile_dir = os.path.join(project_root, "data", "profiles", f"profile_{clean_email}")
    if not os.path.exists(profile_dir):
        os.makedirs(profile_dir, exist_ok=True)
    return profile_dir

def get_browser_launch_params(email, chrome_profile="_tool_profile_", proxy_str=None, task_id=None):
    """
    Trả về (user_data_dir, launch_args) cho launch_persistent_context dựa trên profile được chọn.
    Áp dụng cơ chế Mirror Profile để tránh bị lock file khi Chrome gốc đang chạy.
    """
    import os
    import shutil
    import re
    
    launch_args = {
        "headless": False,
        "channel": "chrome",
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-http2"
        ]
    }
    
    proxy_settings = parse_proxy(proxy_str)
    if proxy_settings:
        launch_args["proxy"] = proxy_settings
        
    if chrome_profile == "_tool_profile_":
        user_data_dir = get_profile_dir(email)
    else:
        # Sử dụng profile Chrome có sẵn trên máy của người dùng
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            chrome_user_data = os.path.join(local_app_data, "Google", "Chrome", "User Data")
            src_profile_dir = os.path.join(chrome_user_data, chrome_profile)
            
            if os.path.exists(src_profile_dir):
                # Tạo thư mục profile tạm thời cho tài khoản này
                clean_email = re.sub(r'[^a-zA-Z0-9@._-]', '_', email)
                src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                project_root = os.path.dirname(src_dir)
                suffix = f"_{task_id}" if task_id is not None else ""
                user_data_dir = os.path.join(project_root, "data", "profiles", f"temp_{clean_email}{suffix}")
                dest_profile_dir = os.path.join(user_data_dir, chrome_profile)
                
                # Xác định thư mục nguồn: Ưu tiên copy từ thư mục persistent temp của "Mở" trình duyệt 
                # (nơi lưu các cookies và session mới nhất sau khi đăng nhập) thay vì LOCALAPPDATA gốc
                persistent_temp_dir = os.path.join(project_root, "data", "profiles", f"temp_{clean_email}")
                if task_id is not None and os.path.exists(persistent_temp_dir):
                    src_user_data_root = persistent_temp_dir
                    src_profile_root = os.path.join(persistent_temp_dir, chrome_profile)
                else:
                    src_user_data_root = chrome_user_data
                    src_profile_root = src_profile_dir

                # 1. Nếu là task chạy tự động (task_id is not None), ta dọn dẹp các tiến trình Chrome cũ
                # và XÓA SẠCH thư mục tạm của task để đảm bảo copy mới 100%, tránh stale/lock files.
                if task_id is not None:
                    try:
                        kill_chrome_processes_by_profile(user_data_dir)
                        if os.path.exists(user_data_dir):
                            shutil.rmtree(user_data_dir)
                    except Exception as clean_err:
                        logging.warning(f"[Browser] Không thể dọn dẹp thư mục tạm task cũ {user_data_dir}: {clean_err}")

                os.makedirs(dest_profile_dir, exist_ok=True)
                os.makedirs(os.path.join(dest_profile_dir, "Network"), exist_ok=True)
                
                # 2. Nếu đang "Mở" trình duyệt trực tiếp (task_id is None) và thư mục lưu trữ temp_{clean_email} đã có session
                # thì bỏ qua việc copy để không bị ghi đè mất phiên đăng nhập đã có.
                dest_pref = os.path.join(dest_profile_dir, "Preferences")
                dest_cookies = os.path.join(dest_profile_dir, "Network", "Cookies")
                is_initialized = os.path.exists(dest_pref) and os.path.exists(dest_cookies)
                
                if task_id is not None or not is_initialized:
                    def copy_file_safe(src, dest):
                        if os.path.exists(src):
                            try:
                                # Nếu chạy task (task_id is not None) -> luôn force copy đè
                                # Nếu click Mở (task_id is None) -> chỉ copy khi đích chưa có hoặc nguồn mới hơn
                                should_copy = (task_id is not None) or (not os.path.exists(dest)) or (os.path.getmtime(src) > os.path.getmtime(dest))
                                if should_copy:
                                    try:
                                        shutil.copy2(src, dest)
                                    except Exception:
                                        # Fallback copy nhị phân thủ công phòng khi file bị lock metadata
                                        with open(src, 'rb') as f_src:
                                            data = f_src.read()
                                        with open(dest, 'wb') as f_dest:
                                            f_dest.write(data)
                            except Exception as copy_err:
                                logging.warning(f"[Browser] Không thể copy {src} sang {dest}: {copy_err}")
                                
                    # Sao chép các tệp quản lý session thiết yếu
                    copy_file_safe(os.path.join(src_user_data_root, "Local State"), os.path.join(user_data_dir, "Local State"))
                    copy_file_safe(os.path.join(src_profile_root, "Preferences"), os.path.join(dest_profile_dir, "Preferences"))
                    copy_file_safe(os.path.join(src_profile_root, "Network", "Cookies"), os.path.join(dest_profile_dir, "Network", "Cookies"))
                    copy_file_safe(os.path.join(src_profile_root, "Web Data"), os.path.join(dest_profile_dir, "Web Data"))
                    copy_file_safe(os.path.join(src_profile_root, "Login Data"), os.path.join(dest_profile_dir, "Login Data"))
                
                launch_args["args"].append(f"--profile-directory={chrome_profile}")
            else:
                # Fallback nếu profile gốc không tồn tại
                user_data_dir = get_profile_dir(email)
        else:
            # Fallback nếu không tìm thấy AppData
            user_data_dir = get_profile_dir(email)
            
    return user_data_dir, launch_args

def get_chrome_path():
    """
    Tìm đường dẫn cài đặt của Google Chrome trên Windows thông qua Registry.
    """
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")
        path, _ = winreg.QueryValue(key, None)
        winreg.CloseKey(key)
        if os.path.exists(path):
            return path
    except Exception:
        pass
        
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")
        path, _ = winreg.QueryValue(key, None)
        winreg.CloseKey(key)
        if os.path.exists(path):
            return path
    except Exception:
        pass
    
    # Các đường dẫn fallback phổ biến
    fallbacks = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")
    ]
    for p in fallbacks:
        if os.path.exists(p):
            return p
    return None

def kill_chrome_processes_by_profile(user_data_dir):
    """
    Tìm và tắt tất cả các tiến trình chrome.exe đang sử dụng thư mục user_data_dir này trên Windows.
    """
    import subprocess
    import os
    import logging
    
    normalized_path = os.path.abspath(user_data_dir).lower()
    
    try:
        # Sử dụng wmic để lấy danh sách tiến trình chrome.exe
        cmd = 'wmic process where "name=\'chrome.exe\'" get ProcessID, CommandLine /format:csv'
        output = subprocess.check_output(cmd, shell=True, text=True, errors='ignore')
        
        for line in output.splitlines():
            line = line.strip()
            if not line or "Node,CommandLine" in line:
                continue
            parts = line.split(',')
            if len(parts) >= 3:
                pid_str = parts[-1].strip()
                command_line = ",".join(parts[1:-1]).lower()
                
                # Nếu command line chứa đường dẫn profile này, ta kill nó
                if normalized_path in command_line:
                    try:
                        subprocess.run(f'taskkill /F /PID {pid_str}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        logging.info(f"[Browser] Đã kill tiến trình Chrome cũ xung đột: PID {pid_str}")
                    except Exception:
                        pass
    except Exception:
        # Fallback dùng PowerShell nếu wmic bị lỗi hoặc không khả dụng
        try:
            ps_cmd = f'Get-CimInstance Win32_Process -Filter "name = \'chrome.exe\'" | Where-Object {{$_.CommandLine -like "*{normalized_path}*"}} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}'
            subprocess.run(["powershell", "-Command", ps_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

def launch_chrome_and_connect(
    p, email, chrome_profile, proxy_str=None, task_id=None, window_slot=None,
    show_browser=None, preserve_profile_data=False,
):
    """
    Khởi chạy Google Chrome gốc qua subprocess với cổng gỡ lỗi từ xa ngẫu nhiên
    và kết nối Playwright qua connect_over_cdp.
    Trả về đối tượng BrowserContext.
    """
    import subprocess
    import time
    import socket
    import logging
    import os

    tile_bounds = None
    if window_slot is not None:
        try:
            slot_index, slot_count = window_slot
            tile_bounds = calculate_tiled_window_bounds(slot_index, slot_count)
            logging.info(
                "[Browser] Window slot %s/%s -> bounds=%s",
                int(slot_index) + 1, slot_count, tile_bounds,
            )
        except Exception as exc:
            logging.warning("[Browser] window_slot không hợp lệ %r: %s", window_slot, exc)
    hide_browser = task_id is not None and not bool(show_browser) and tile_bounds is None
    
    chrome_path = get_chrome_path()
    if not chrome_path:
        raise Exception("Không tìm thấy Google Chrome được cài đặt trên máy tính của bạn.")
        
    # Gemini cần giữ cookie/lịch sử qua nhiều batch, nên dùng profile bền vững thay vì
    # mirror riêng theo task. Ảnh/Video không truyền cờ này và giữ nguyên hành vi cũ.
    profile_task_id = None if preserve_profile_data else task_id
    user_data_dir, launch_args = get_browser_launch_params(
        email, chrome_profile, proxy_str, profile_task_id
    )
    logging.info(
        "[Browser] Profile policy: preserve=%s; task_id=%s; profile_task_id=%s; user_data_dir=%s",
        preserve_profile_data, task_id, profile_task_id, user_data_dir,
    )
    
    # Dọn dẹp tiến trình Chrome cũ xung đột trước khi mở mới
    try:
        kill_chrome_processes_by_profile(user_data_dir)
    except Exception as e:
        logging.warning(f"[Browser] Không thể dọn dẹp tiến trình Chrome cũ: {e}")
    
    # Tìm một cổng TCP trống ngẫu nhiên
    def find_free_port():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        return port
        
    port = find_free_port()
    logging.info(f"[Browser] Khởi chạy Chrome gốc trên cổng debug: {port}...")
    
    # Chuẩn bị đối số khởi chạy Chrome
    # Không dùng cờ '--enable-automation' để tránh bị phát hiện
    chrome_args = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-client-side-phishing-detection",
        "--disable-default-apps",
        "--disable-sync",
        "--hide-crash-restore-bubble",
        "--disable-session-crashed-bubble",
        "--enable-logging=stderr",
        "--log-level=1",
    ]
    
    # Nếu dùng proxy, thêm tham số --proxy-server
    if proxy_str:
        proxy_settings = parse_proxy(proxy_str)
        if proxy_settings and "server" in proxy_settings:
            chrome_args.append(f"--proxy-server={proxy_settings['server']}")
            
    if chrome_profile != "_tool_profile_":
        chrome_args.append(f"--profile-directory={chrome_profile}")
        
    # Thêm cờ disable-blink-features để ẩn webdriver
    chrome_args.append("--disable-blink-features=AutomationControlled")
    
    # Thêm cờ khởi chạy ở chế độ thu nhỏ (minimize) để tránh cản trở công việc người dùng
    if hide_browser:
        chrome_args.append("--start-minimized")
        chrome_args.append("--window-position=-32000,-32000")
        chrome_args.append("--window-size=1280,720")
    elif tile_bounds is not None:
        tile_x, tile_y, tile_width, tile_height = tile_bounds
        chrome_args.append(f"--window-position={tile_x},{tile_y}")
        chrome_args.append(f"--window-size={tile_width},{tile_height}")
    
    # Khởi chạy Google Chrome
    startupinfo = None
    prev_active_hwnd = None
    if hide_browser and os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 7  # SW_SHOWMINNOACTIVE
        try:
            import win32gui
            prev_active_hwnd = win32gui.GetForegroundWindow()
        except Exception:
            pass

    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(src_dir)
    diagnostics_dir = os.path.join(project_root, "logs", "chrome")
    os.makedirs(diagnostics_dir, exist_ok=True)
    safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(task_id or email or "chrome"))
    launch_stamp = time.strftime("%Y%m%d_%H%M%S")
    chrome_log_path = os.path.join(
        diagnostics_dir, f"chrome_{safe_task}_{launch_stamp}_port{port}.log"
    )
    chrome_log_stream = open(chrome_log_path, "ab", buffering=0)
    proc = subprocess.Popen(
        chrome_args,
        stdout=chrome_log_stream,
        stderr=chrome_log_stream,
        startupinfo=startupinfo
    )
    register_chrome_pid(proc.pid)
    runtime = {
        "process": proc,
        "port": port,
        "started_at": time.monotonic(),
        "user_data_dir": user_data_dir,
        "log_path": chrome_log_path,
        "log_stream": chrome_log_stream,
        "close_requested": False,
        "monitor_stop": threading.Event(),
    }
    logging.info(
        "[Browser] Runtime Chrome: pid=%s; port=%s; profile=%s; stderr=%s",
        proc.pid, port, user_data_dir, chrome_log_path,
    )

    def monitor_chrome_process():
        while not runtime["monitor_stop"].wait(1.0):
            exit_code = proc.poll()
            if exit_code is not None:
                level = logging.INFO if runtime["close_requested"] else logging.ERROR
                logging.log(
                    level,
                    "[Browser Monitor] Chrome PID %s đã thoát; exit_code=%s; "
                    "uptime=%.1fs; close_requested=%s; cdp_port=%s; cdp_open=%s; stderr=%s",
                    proc.pid, exit_code, time.monotonic() - runtime["started_at"],
                    runtime["close_requested"], port, _is_local_port_open(port),
                    chrome_log_path,
                )
                break

    threading.Thread(
        target=monitor_chrome_process,
        name=f"chrome-monitor-{proc.pid}",
        daemon=True,
    ).start()

    # Chỉ thu nhỏ (minimize) nếu là task chạy tự động
    if hide_browser:
        threading.Thread(
            target=_minimize_chrome_window,
            args=(proc.pid, prev_active_hwnd),
            daemon=True
        ).start()
    elif tile_bounds is not None:
        threading.Thread(
            target=_tile_chrome_window,
            args=(proc.pid, tile_bounds),
            daemon=True,
        ).start()
    
    # Kiểm tra xem cổng debug đã mở chưa
    deadline = time.time() + 15
    connected = False
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(('127.0.0.1', port))
            s.close()
            connected = True
            break
        except Exception:
            time.sleep(0.5)
            
    if not connected:
        try:
            proc.kill()
        except:
            pass
        unregister_chrome_pid(proc.pid)
        runtime["monitor_stop"].set()
        chrome_log_stream.close()
        raise Exception(f"Không thể khởi động cổng debug trên Chrome (Port: {port}).")
        
    # Kết nối Playwright tới cổng debug của Chrome
    try:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        # Mặc định, persistent profile của Chrome khởi động sẽ tự tạo ra ít nhất 1 context
        context = browser.contexts[0]
    except Exception as ex:
        try:
            proc.kill()
        except:
            pass
        unregister_chrome_pid(proc.pid)
        runtime["monitor_stop"].set()
        chrome_log_stream.close()
        raise Exception(f"Không thể kết nối Playwright tới Chrome qua CDP: {ex}")

    context._auto_flow_chrome_runtime = runtime
        
    # Patch hàm context.close() để đóng cả trình duyệt và kill process
    original_close = context.close
    
    def close_all_resources():
        runtime["close_requested"] = True
        logging.info(
            "[Browser] Đóng kết nối trình duyệt và dọn dẹp tiến trình Chrome; "
            "pid=%s; alive_before=%s; exit_code_before=%s; cdp_open_before=%s; uptime=%.1fs",
            proc.pid, proc.poll() is None, proc.poll(), _is_local_port_open(port),
            time.monotonic() - runtime["started_at"],
        )
        try:
            original_close()
        except:
            pass
        try:
            browser.close()
        except:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except:
            try:
                proc.kill()
            except:
                pass
        runtime["monitor_stop"].set()
        logging.info(
            "[Browser] Đã đóng Chrome; pid=%s; exit_code_after=%s; cdp_open_after=%s; stderr=%s",
            proc.pid, proc.poll(), _is_local_port_open(port), chrome_log_path,
        )
        try:
            chrome_log_stream.close()
        except Exception:
            pass
        
        # Chỉ xóa profile theo-task của Flow Ảnh/Video. Gemini dùng profile bền vững,
        # phải giữ nguyên cookie, browser history và session sau khi đóng Chrome.
        if task_id is not None and not preserve_profile_data:
            import shutil
            for _ in range(5):
                try:
                    if os.path.exists(user_data_dir):
                        shutil.rmtree(user_data_dir)
                    logging.info(f"[Browser] Đã dọn dẹp thư mục tạm thời: {user_data_dir}")
                    break
                except Exception:
                    time.sleep(0.5)
        elif preserve_profile_data:
            logging.info(
                "[Browser] Giữ nguyên profile sau khi đóng: %s (cookie/history/session không bị xóa)",
                user_data_dir,
            )
                
    original_close_wrap = close_all_resources
    _captured_pid = proc.pid
    def close_and_release_resources():
        original_close_wrap()
        unregister_chrome_pid(_captured_pid)

    context.close = close_and_release_resources
    return context

def login_and_save_cookies(proxy_str=None, email=None, chrome_profile="_tool_profile_"):
    """
    Mở Chrome với profile được chỉ định (profile của tool hoặc của máy),
    cho phép người dùng tự đăng nhập và tự động cập nhật session/cookies.
    """
    import time
    if not email:
        raise Exception("Yêu cầu nhập Email để quản lý Profile trình duyệt.")
        
    with sync_playwright() as p:
        try:
            context = launch_chrome_and_connect(p, email, chrome_profile, proxy_str)
            page = context.pages[0] if context.pages else context.new_page()
            
            # Điều hướng đến trang đăng nhập Google
            page.goto("https://accounts.google.com/signin", timeout=60000)
            
            # Đợi tối đa 5 phút (300 giây) để người dùng đăng nhập thủ công
            start_time = time.time()
            logged_in = False
            
            while time.time() - start_time < 300:
                if page.is_closed():
                    break
                
                try:
                    current_url = page.url
                    # Nếu chuyển hướng sang trang quản lý tài khoản hoặc có cookie xác thực
                    if "myaccount.google.com" in current_url or "SignOutOptions" in current_url:
                        logged_in = True
                        break
                    
                    cookies = context.cookies()
                    cookie_names = {c['name'] for c in cookies}
                    if "SID" in cookie_names and "HSID" in cookie_names:
                        if "signin" not in current_url and "identifier" not in current_url:
                            logged_in = True
                            break
                except Exception:
                    pass
                
                page.wait_for_timeout(1000)
            
            # Thu thập cookies sau khi đăng nhập thành công
            cookies = context.cookies()
            cookie_names = {c['name'] for c in cookies}
            
            if "SID" in cookie_names:
                # Tự động kiểm tra loại tài khoản (FREE, PRO, ULTRA) và số lượng credits còn lại trên Google Labs
                detected_type = "FREE"
                detected_credits = 0
                try:
                    page.goto("https://labs.google/fx/tools/image-fx", timeout=30000)
                    page.wait_for_timeout(3000)
                    body_text = page.locator("body").inner_text().lower()
                    if "ultra" in body_text:
                        detected_type = "ULTRA"
                    elif "pro" in body_text:
                        detected_type = "PRO"
                    
                    # Trích xuất số lượng credit bằng JS script chạy trực tiếp trên trang
                    res_credits = page.evaluate("""() => {
                        const text = document.body.innerText || "";
                        const patterns = [
                            /(\\d+[\\d,.\\s]*)\\s*khoản tín dụng/i,
                            /(\\d+[\\d,.\\s]*)\\s*tín dụng/i,
                            /(\\d+[\\d,.\\s]*)\\s*credits?\\b/i,
                            /credits?\\s*:\\s*(\\d+[\\d,.\\s]*)/i,
                            /tín dụng\\s*:\\s*(\\d+[\\d,.\\s]*)/i
                        ];
                        for (const pattern of patterns) {
                            const match = text.match(pattern);
                            if (match) {
                                const val = parseInt(match[1].replace(/[,.\\s]/g, ""), 10);
                                if (val >= 0 && val <= 100000) return val;
                            }
                        }
                        
                        const creditEls = Array.from(document.querySelectorAll('*')).filter(el => {
                            const label = (el.getAttribute('aria-label') || '').toLowerCase();
                            const textContent = (el.textContent || '').toLowerCase();
                            return label.includes('credit') || label.includes('tín dụng') || textContent.includes('credit') || textContent.includes('tín dụng');
                        });
                        for (const el of creditEls) {
                            const textContent = el.textContent || '';
                            const numMatch = textContent.match(/(\\d+[\\d,.\\s]*)/);
                            if (numMatch) {
                                const val = parseInt(numMatch[1].replace(/[,.\\s]/g, ""), 10);
                                if (val >= 0 && val <= 100000) return val;
                            }
                        }
                        return null;
                    }""")
                    if res_credits is not None:
                        detected_credits = res_credits
                        logging.info(f"[Browser] Đã phát hiện số dư credit của tài khoản: {detected_credits}")
                except Exception as ex:
                    import logging
                    logging.warning(f"[Browser] Không thể tự kiểm tra loại tài khoản hoặc số dư credit: {ex}")
                
                # Đóng context an toàn
                context.close()
                return json.dumps(cookies), detected_type, detected_credits
            else:
                raise Exception("Đăng nhập chưa hoàn tất hoặc thiếu cookie SID sau 5 phút.")
                
        except Exception as e:
            raise Exception(f"Lỗi đăng nhập: {str(e)}")

def get_local_chrome_profiles():
    """
    Quét danh sách các profile Chrome hiện có trên máy tính Windows.
    Đọc từ file Local State để lấy tên hiển thị của các profile.
    """
    import os
    import json
    
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return [("_tool_profile_", "Profile riêng của Tool"), ("Default", "Default")]
        
    user_data_dir = os.path.join(local_app_data, "Google", "Chrome", "User Data")
    local_state_path = os.path.join(user_data_dir, "Local State")
    
    profiles = []
    # Đọc cấu hình các profile từ Local State của Chrome
    if os.path.exists(local_state_path):
        try:
            with open(local_state_path, "r", encoding="utf-8", errors="ignore") as f:
                state = json.load(f)
            info_cache = state.get("profile", {}).get("info_cache", {})
            for folder, info in info_cache.items():
                name = info.get("name", folder)
                profiles.append((folder, f"Local: {name} ({folder})"))
        except Exception:
            pass
            
    # Dự phòng: Quét trực tiếp thư mục nếu đọc file Local State lỗi
    if not profiles:
        if os.path.exists(user_data_dir):
            for item in os.listdir(user_data_dir):
                if item == "Default" or item.startswith("Profile "):
                    item_path = os.path.join(user_data_dir, item)
                    if os.path.isdir(item_path):
                        profiles.append((item, f"Local: {item}"))
                        
    # Đảm bảo có ít nhất Default profile
    if not any(p[0] == "Default" for p in profiles):
        profiles.append(("Default", "Local: Default"))
        
    # Sắp xếp các profile local theo tên hiển thị
    profiles.sort(key=lambda x: x[1])
    
    # Luôn chèn profile riêng biệt của tool lên đầu danh sách
    profiles.insert(0, ("_tool_profile_", "Profile riêng của Tool"))
    return profiles

def parse_proxy(proxy_str):
    if not proxy_str:
        return None
    proxy_str = proxy_str.strip()
    scheme = "http"
    for s in ["http://", "https://", "socks5://"]:
        if proxy_str.lower().startswith(s):
            scheme = s[:-3]
            proxy_str = proxy_str[len(s):]
            break
    parts = proxy_str.split(':')
    if len(parts) == 4:
        host, port, user, password = parts
        return {
            "server": f"{scheme}://{host}:{port}",
            "username": user,
            "password": password
        }
    elif len(parts) == 2:
        host, port = parts
        return {
            "server": f"{scheme}://{host}:{port}"
        }
    return None

def update_account_credits_and_type_from_page(page, account_id):
    """
    Tự động trích xuất loại tài khoản (FREE, PRO, ULTRA) và số lượng credits
    còn lại từ trang Google Labs và cập nhật trực tiếp vào cơ sở dữ liệu.
    """
    try:
        from data.database import SessionLocal
        from data.models import Account
        
        # Đợi trang ổn định một chút để các phần tử HTML render xong
        page.wait_for_timeout(1000)
        
        # 1. Phát hiện loại tài khoản
        detected_type = "FREE"
        body_text = ""
        try:
            body_text = page.locator("body").inner_text().lower()
            if "ultra" in body_text:
                detected_type = "ULTRA"
            elif "pro" in body_text:
                detected_type = "PRO"
        except:
            pass
            
        # 2. Phát hiện số lượng credits
        res_credits = None
        try:
            res_credits = page.evaluate("""() => {
                const text = document.body.innerText || "";
                const patterns = [
                    /(\\d+[\\d,.\\s]*)\\s*khoản tín dụng/i,
                    /(\\d+[\\d,.\\s]*)\\s*tín dụng/i,
                    /(\\d+[\\d,.\\s]*)\\s*credits?\\b/i,
                    /credits?\\s*:\\s*(\\d+[\\d,.\\s]*)/i,
                    /tín dụng\\s*:\\s*(\\d+[\\d,.\\s]*)/i
                ];
                for (const pattern of patterns) {
                    const match = text.match(pattern);
                    if (match) {
                        const val = parseInt(match[1].replace(/[,.\\s]/g, ""), 10);
                        if (val >= 0 && val <= 100000) return val;
                    }
                }
                
                const creditEls = Array.from(document.querySelectorAll('*')).filter(el => {
                    const label = (el.getAttribute('aria-label') || '').toLowerCase();
                    const textContent = (el.textContent || '').toLowerCase();
                    return label.includes('credit') || label.includes('tín dụng') || textContent.includes('credit') || textContent.includes('tín dụng');
                });
                for (const el of creditEls) {
                    const textContent = el.textContent || '';
                    const numMatch = textContent.match(/(\\d+[\\d,.\\s]*)/);
                    if (numMatch) {
                        const val = parseInt(numMatch[1].replace(/[,.\\s]/g, ""), 10);
                        if (val >= 0 && val <= 100000) return val;
                    }
                }
                return null;
            }""")
        except:
            pass
            
        # 3. Cập nhật cơ sở dữ liệu
        db = SessionLocal()
        acc = db.query(Account).filter(Account.id == account_id).first()
        if acc:
            acc.account_type = detected_type
            if res_credits is not None:
                acc.credits = res_credits
                logging.info(f"[Credits] Cập nhật tài khoản ID {account_id} thành công: {detected_type} ({res_credits} credits)")
            else:
                logging.info(f"[Credits] Cập nhật tài khoản ID {account_id} thành công: {detected_type} (không phát hiện số credits)")
            db.commit()
        db.close()
    except Exception as e:
        logging.warning(f"[Credits] Lỗi tự động cập nhật credits cho tài khoản ID {account_id}: {e}")
