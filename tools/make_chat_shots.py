"""生成 chat-demo（MiMo Local 新前端）的展示截图。

起 llama-server + 托管 webui/，用 Playwright 截真实对话。
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


QUESTION = "用 Python 写一个判断回文的函数，忽略大小写和标点。代码后面用两句话说明思路。"


def ask(page, q):
    """发一条消息，等流式结束。"""
    ta = page.query_selector("textarea")
    if not ta:
        return False
    ta.click()
    ta.fill(q)
    page.wait_for_timeout(300)
    ta.press("Enter")
    t0 = time.time()
    last, stable = "", 0
    while time.time() - t0 < 240:
        page.wait_for_timeout(1000)
        try:
            cur = page.inner_text("body")
        except Exception:
            continue
        if cur == last and len(cur) > 150:
            stable += 1
            if stable >= 5:
                break
        else:
            stable = 0
            last = cur
    return len(last) > 150


def main() -> int:
    print("起 llama-server…")
    log = open(Path(r"D:\mimo-desktop\logs\shot-engine.log"), "wb")
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
        print(f"  ✓ 静态服务 http://127.0.0.1:{PORT}")

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(channel="msedge", headless=True)

            # ---- 深色主题主图 ----
            page = b.new_page(viewport={"width": 1440, "height": 900},
                              device_scale_factor=2, color_scheme="dark")
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle",
                      timeout=60000)
            page.wait_for_timeout(2500)

            # 欢迎页（新对话）
            out1 = OUT / "shot-chat-welcome.png"
            page.screenshot(path=str(out1), type="png")
            print(f"  ✓ {out1.name}  {out1.stat().st_size / 1024:.0f} KB")

            # 对话后
            if ask(page, QUESTION):
                page.wait_for_timeout(1500)
                out2 = OUT / "shot-chat.png"
                page.screenshot(path=str(out2), type="png")
                print(f"  ✓ {out2.name}  {out2.stat().st_size / 1024:.0f} KB")
            else:
                print("  ⚠ 对话没完成")

            # ---- 浅色主题 ----
            page2 = b.new_page(viewport={"width": 1440, "height": 900},
                               device_scale_factor=2, color_scheme="light")
            page2.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle",
                       timeout=60000)
            page2.wait_for_timeout(2500)
            if ask(page2, "解释一下什么是幂等性，一句话。"):
                page2.wait_for_timeout(1200)
                out3 = OUT / "shot-chat-light.png"
                page2.screenshot(path=str(out3), type="png")
                print(f"  ✓ {out3.name}  {out3.stat().st_size / 1024:.0f} KB")

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
