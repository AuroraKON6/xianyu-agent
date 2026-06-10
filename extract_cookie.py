"""从Playwright浏览器提取闲鱼Cookie

打开浏览器 → 你登录（扫码+人脸） → 访问 http://localhost:9999/done → 提取Cookie
"""
import asyncio
import json
import re
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
ENV_FILE = PROJECT_DIR / ".env"
COOKIE_FILE = PROJECT_DIR / "data" / "browser_cookies.json"

# 信号标志
login_done = threading.Event()


class SignalHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/done":
            login_done.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("OK! 正在提取Cookie...".encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("请完成登录后访问 http://localhost:9999/done".encode("utf-8"))

    def log_message(self, format, *args):
        pass  # 静默


def start_signal_server():
    server = HTTPServer(("127.0.0.1", 9999), SignalHandler)
    server.timeout = 1
    while not login_done.is_set():
        server.handle_request()
    server.server_close()


async def main():
    from playwright.async_api import async_playwright

    print("=" * 50)
    print("闲鱼Cookie获取工具")
    print("=" * 50)
    print()

    # 启动信号服务器
    threading.Thread(target=start_signal_server, daemon=True).start()

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    context = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await context.new_page()

    print("打开闲鱼登录页...")
    await page.goto("https://www.goofish.com/login", wait_until="networkidle", timeout=30000)

    print()
    print("┌────────────────────────────────────────────┐")
    print("│  请在浏览器中完成登录:                      │")
    print("│    1. 扫码                                  │")
    print("│    2. 人脸识别                               │")
    print("│    3. 看到闲鱼首页后                        │")
    print("│    4. 访问: http://localhost:9999/done       │")
    print("└────────────────────────────────────────────┘")
    print()
    print("等待登录完成...")

    # 等待信号
    while not login_done.is_set():
        await asyncio.sleep(1)

    print()
    print("收到信号，提取Cookie...")
    await asyncio.sleep(2)

    cookies = await context.cookies()
    goofish = [c for c in cookies if "goofish" in c.get("domain", "")]
    if not goofish:
        goofish = cookies

    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in goofish)
    print(f"提取到 {len(goofish)} 个Cookie")

    # 保存
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(json.dumps({"default": cookie_str}, indent=2), encoding="utf-8")
    print(f"已保存到: {COOKIE_FILE}")

    # 更新.env
    if ENV_FILE.exists():
        content = ENV_FILE.read_text(encoding="utf-8")
        content = re.sub(r"XIANYU_COOKIES=.*", f"XIANYU_COOKIES={cookie_str}", content)
        ENV_FILE.write_text(content, encoding="utf-8")
        print(f"已更新: {ENV_FILE}")

    # 登录状态
    login_file = PROJECT_DIR / "data" / "browser_login.json"
    login_file.parent.mkdir(parents=True, exist_ok=True)
    login_file.write_text(json.dumps({
        "cookie_id": "default", "logged_in": True,
        "login_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, indent=2), encoding="utf-8")

    print()
    print("完成！重启服务器即可使用。")
    print("  python -m xianyu_agent")

    await browser.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
