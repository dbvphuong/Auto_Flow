import os
import sys
import argparse
from playwright.sync_api import sync_playwright

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="default_selector_tool")
    parser.add_argument("--profile", default="_tool_profile_")
    parser.add_argument("--proxy", default="")
    parser.add_argument("--url", default="https://labs.google/fx/tools/image-fx")
    args = parser.parse_args()
    
    # Thiết lập PYTHONPATH để import được các module từ src
    current_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.append(os.path.dirname(current_dir))
    
    from core.browser_manager import launch_chrome_and_connect
    
    proxy_str = args.proxy if args.proxy else None
    
    # Kích hoạt chế độ Playwright Inspector (Codegen)
    os.environ["PWDEBUG"] = "1"
    
    print(f"[Inspector] Launching with email={args.email}, profile={args.profile}")
    
    with sync_playwright() as p:
        try:
            context = launch_chrome_and_connect(p, args.email, args.profile, proxy_str)
            page = context.pages[0] if context.pages else context.new_page()
            
            # Điều hướng tới URL
            try:
                page.goto(args.url, timeout=60000)
            except Exception as ex:
                print(f"[Inspector] Navigation error or stopped: {ex}")
            
            # Giữ cửa sổ mở cho đến khi page bị đóng
            while not page.is_closed():
                page.wait_for_timeout(1000)
                
            context.close()
        except Exception as e:
            print(f"[Inspector] Playwright launch error: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
