"""生成 chat-demo 的多场景截图：浅色主题 / 多会话 / 数学公式+Mermaid。

这些都是新界面独有的能力，老素材里没有。
"""
import http.server
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

ENGINE = Path(r"D:\llama.cpp\llama-b11270-bin-win-cuda-13.4-x64\llama-server.exe")
MODEL_DIR = Path(r"D:\models\MiMo-9B-GGUF")
MODEL = MODEL_DIR / "MiMo-V2.6-Distill-Qwen-9B-Q5_K_M.gguf"
MMPROJ = MODEL_DIR / "mmproj-MiMo-V2.6-Distill-Qwen-9B-f16.gguf"
WEBUI = Path(r"D:\mimo-desktop\webui")
OUT = Path(r"D:\mimo-desktop\docs")
PORT = 8099
LLAMA_PORT = 8080


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEBUI), **kw)

    def log_message(self, *a):
        pass


def wait_llama(timeout=120):
    import urllib.request
    for _ in range(timeout):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{LLAMA_PORT}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def ask(page, q, min_len=200):
    ta = page.query_selector("textarea")
    if not ta:
        return False
    ta.click()
    ta.fill(q)
    page.wait_for_timeout(300)
    ta.press("Enter")
    t0 = time.time()
    last, stable = "", 0
    while time.time() - t0 < 300:
        page.wait_for_timeout(1000)
        try:
            cur = page.inner_text("body")
        except Exception:
            continue
        if cur == last and len(cur) > min_len:
            stable += 1
            if stable >= 5:
                return True
        else:
            stable = 0
            last = cur
    return len(last) > min_len


def main() -> int:
    print("起 llama-server…")
    log = open(Path(r"D:\mimo-desktop\logs\shots2-engine.log"), "wb")
    eng = subprocess.Popen(
        [str(ENGINE), "-m", str(MODEL), "--mmproj", str(MMPROJ),
         "--host", "127.0.0.1", "--port", str(LLAMA_PORT),
         "-c", "8192", "-ngl", "99", "--flash-attn", "on",
         "-ctk", "q8_0", "-ctv", "q8_0", "--reasoning-format", "deepseek"],
        stdout=subprocess.DEVNULL, stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW)
    srv = None
    try:
        if not wait_llama():
            print("引擎没起来", file=sys.stderr)
            return 1
        print("  ✓ 引擎就绪")
        socketserver.TCPServer.allow_reuse_address = True
        srv = socketserver.TCPServer(("127.0.0.1", PORT), Quiet)
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(channel="msedge", headless=True)

            # ---- 1. 浅色主题 + 多轮对话 ----
            page = b.new_page(viewport={"width": 1440, "height": 900},
                              device_scale_factor=2, color_scheme="light")
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2500)
            ask(page, "用一句话解释什么是递归。")
            page.wait_for_timeout(800)
            ask(page, "再举一个 Python 的例子。")
            page.wait_for_timeout(1500)
            o1 = OUT / "shot-chat-light.png"
            page.screenshot(path=str(o1), type="png")
            print(f"  ✓ {o1.name}  {o1.stat().st_size / 1024:.0f} KB")

            # ---- 2. 深色 + 数学公式 / 表格 ----
            page2 = b.new_page(viewport={"width": 1440, "height": 900},
                               device_scale_factor=2, color_scheme="dark")
            page2.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle", timeout=60000)
            page2.wait_for_timeout(2500)
            ask(page2, "用 Markdown 表格列出 Python 三种基本数据类型，再用 LaTeX 写出它们的英文名缩写公式示例。")
            page2.wait_for_timeout(1500)
            o2 = OUT / "shot-chat-markdown.png"
            page2.screenshot(path=str(o2), type="png")
            print(f"  ✓ {o2.name}  {o2.stat().st_size / 1024:.0f} KB")

            # ---- 3. 多会话侧边栏 ----
            # 再开两个对话，让左侧列表有内容
            page2.keyboard.press("Control+n")
            page2.wait_for_timeout(1200)
            ask(page2, "写一个 Python 的斐波那契函数。", min_len=150)
            page2.wait_for_timeout(1000)
            o3 = OUT / "shot-chat-sessions.png"
            page2.screenshot(path=str(o3), type="png")
            print(f"  ✓ {o3.name}  {o3.stat().st_size / 1024:.0f} KB")

            b.close()
        return 0
    finally:
        if srv:
            srv.shutdown()
        eng.terminate()
        try:
            eng.wait(timeout=10)
        except Exception:
            eng.kill()
        log.close()
        print("已清理")


if __name__ == "__main__":
    sys.exit(main())
