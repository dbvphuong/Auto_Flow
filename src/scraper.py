import sys
import os

# Add src to path so we can import modules
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from playwright.sync_api import sync_playwright
import json
from data.database import SessionLocal
from data.models import Account

db = SessionLocal()
account = db.query(Account).filter(Account.is_active == True).order_by(Account.position.asc()).first()

with sync_playwright() as p:
    launch_args = {
        "headless": False,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-http2"
        ]
    }
    browser = p.chromium.launch(**launch_args)
    context = browser.new_context()
    context.add_cookies(json.loads(account.cookies_json))
    page = context.new_page()
    page.goto("https://labs.google/fx/tools/image-fx", timeout=60000)
    page.wait_for_timeout(5000)
    
    search_texts = [
        "Sign in", "Đăng nhập", "Sign In", 
        "Continue", "Tiếp tục", account.email, 
        "Get started with Flow",
        "Got it", "Đã hiểu", "Next", "Tiếp", "Done", "Xong", "Bắt đầu",
        "Dự án mới", "New project", "New Project", "Create project", "Tạo dự án"
    ]
    for _ in range(3):
        frames = [page] + page.frames
        for frame in frames:
            try:
                exact_btn = frame.locator('#sign-in-now-button')
                if exact_btn.is_visible():
                    exact_btn.click(timeout=3000, force=True)
                    page.wait_for_timeout(4000)
            except Exception:
                pass
            for text in search_texts:
                if not text: continue
                try:
                    btns = frame.get_by_text(text, exact=False)
                    for i in range(btns.count()):
                        btn = btns.nth(i)
                        if btn.is_visible():
                            btn.click(timeout=1000, force=True)
                            page.wait_for_timeout(3000)
                except Exception:
                    pass
                    
    page.wait_for_timeout(5000)
    
    with open("flow_workspace.html", "w", encoding="utf-8") as f:
        f.write(page.content())
        
    print("DONE DUMPING")
    browser.close()
