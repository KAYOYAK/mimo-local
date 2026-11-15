"""用聊天界面（chat-demo + 桥）生成三合一素材。

替换掉最后几张「编程界面」的旧图：
  screenshot.png    看图问答   -> 聊天界面发图 + OCR
  shot-tools.png    工具调用   -> 聊天界面 ✓ bash
  shot-selfcheck.png  保留（自检还是桌面侧能力）

桥(8766) 后端是 MiMo Code，所以这里截出来的就是真实的三引擎同时在线。
"""
import base64
import http.server
import json
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"D:\mimo-desktop")
sys.path.insert(0, str(ROOT))

from core import agent as ag
from core import agent_bridge as ab

WORKDIR = Path(r"D:\projects\demo")
WEBUI = ROOT / "webui"
DOCS = ROOT / "docs"
IMG = DOCS / "测试图.jpg"
PORT = 8099
ENGINE = Path(r"D:\llama.cpp\llama-b11270-bin-win-cuda-13.4-x64\llama-server.exe")
MDIR = Path(r"D:\models\MiMo-9B-GGUF")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEBUI), **kw)

    def log_message(self, *a):
        pass


def wait_http(url, timeout=240):
    for _ in range(timeout):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def start_llama():
    log = open(ROOT / "logs" / "tri-llama.log", "wb")
    p = subprocess.Popen(
        [str(ENGINE), "-m", str(MDIR / "MiMo-V2.6-Distill-Qwen-9B-Q5_K_M.gguf"),
         "--mmproj", str(MDIR / "mmproj-MiMo-V2.6-Distill-Qwen-9B-f16.gguf"),
         "--host", "127.0.0.1", "--port", "8080", "-c", "65536", "-ngl", "99",
         "--flash-attn", "on", "-ctk", "q8_0", "-ctv", "q8_0",
         "--reasoning-format", "deepseek"],
        stdout=subprocess.DEVNULL, stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if not wait_http("http://127.0.0.1:8080/health"):
        raise RuntimeError("9B 没起来")
    return p, log


def ask(page, text=None, image_b64=None, timeout=420):
    ta = page.query_selector("textarea")
    if not ta:
        return False
    ta.click()
    if image_b64:
        # chat-demo 里是隐藏的 <input type="file">（fileInputRef），
        # 点按钮会走 file chooser。直接 set_input_files 更可靠。
        try:
            # 页面刚加载时 input 可能还没挂上，等它出现
            inp = None
            for _ in range(15):
                inp = page.query_selector('input[type=file]')
                if inp:
                    break
                page.wait_for_timeout(1000)
            if not inp:
                print("    找不到 input[type=file]")
            else:
                inp.set_input_files(str(IMG))
                # 附件要做 base64 转换（本地模式走 blobToDataUrl），
                # 等 4 秒不够：还没变成 done 时发送按钮是禁用的，
                # 按 Enter 会被静默吞掉。等缩略图出现且发送可用。
                n_before = len(page.query_selector_all("img"))
                for _ in range(25):
                    page.wait_for_timeout(1000)
                    n_now = len(page.query_selector_all("img"))
                    if n_now > n_before:
                        break
                page.wait_for_timeout(3000)      # 等 base64 转换完成
                n_after = len(page.query_selector_all("img"))
                print(f"    已附加图片（img {n_before} -> {n_after}）")
        except Exception as e:
            print(f"    附件失败: {e}")
    if text:
        ta.fill(text)
        page.wait_for_timeout(200)
    ta.press("Enter")
    t0 = time.time()
    last, stable = "", 0
    while time.time() - t0 < timeout:
        page.wait_for_timeout(1000)
        try:
            cur = page.inner_text("body")
        except Exception:
            continue
        # OCR 要跑 20+ 秒，桥才把消息发给 MiMo Code，然后模型还要处理。
        # 稳定阈值从 6 提到 10，避免在「思考中」就截图。
        if cur == last and len(cur) > 400:
            stable += 1
            if stable >= 10:
                return True
        else:
            stable = 0
            last = cur
    return False


def main() -> int:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    print("起 9B（64K）…")
    llama, llama_log = start_llama()
    print("  ✓ 9B")

    print("起 OCR…")
    from core import engine as eng
    from config import OCR_PORT

    ocr = eng.make_ocr_engine()
    try:
        ocr.start()
        wait_http(f"http://127.0.0.1:{OCR_PORT}/health", 180)
        print("  ✓ OCR")
    except Exception as e:
        print(f"  ⚠ OCR: {e}")

    print("起 MiMo Code…")
    server = ag.AgentServer(4096)
    server.start(workdir=WORKDIR)
    ab.set_agent(server)
    print("  ✓ MiMo Code")

    print("起桥…")
    bridge = ab.AgentBridge(WORKDIR, 8766)
    if not bridge.start():
        print("  ✗ 桥启动失败")
        return 1
    print("  ✓ 桥 8766")

    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", PORT), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"  ✓ 静态 {PORT}")

    from playwright.sync_api import sync_playwright

    b64 = None
    if IMG.is_file():
        b64 = base64.b64encode(IMG.read_bytes()).decode()
        print(f"  测试图 {IMG.name}")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)

        # ---- 1. 工具调用 ----
        page = browser.new_page(viewport={"width": 1440, "height": 900},
                                device_scale_factor=2, color_scheme="dark")
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        print("问工具调用…")
        ok = ask(page, "列出当前工作目录下有哪些文件，用一句话说。")
        print(f"  {'✓' if ok else '⚠'} 完成")
        page.wait_for_timeout(1500)
        out1 = DOCS / "shot-three-tools.png"
        page.screenshot(path=str(out1), type="png")
        print(f"  ✓ {out1.name}  {out1.stat().st_size / 1024:.0f} KB")

        # ---- 2. 看图 OCR ----
        page2 = browser.new_page(viewport={"width": 1440, "height": 900},
                                 device_scale_factor=2, color_scheme="dark")
        page2.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle", timeout=60000)
        page2.wait_for_timeout(3000)
        if b64:
            print("发图 + 问题…")
            ok = ask(page2, "这张图里是什么？总共多少钱？", image_b64=b64)
            print(f"  {'✓' if ok else '⚠'} 完成")
            page2.wait_for_timeout(1500)
            out2 = DOCS / "shot-three-ocr.png"
            page2.screenshot(path=str(out2), type="png")
            print(f"  ✓ {out2.name}  {out2.stat().st_size / 1024:.0f} KB")

        browser.close()

    srv.shutdown()
    bridge.stop()
    server.stop()
    try:
        ocr.stop()
    except Exception:
        pass
    llama.terminate()
    try:
        llama.wait(timeout=10)
    except Exception:
        llama.kill()
    llama_log.close()
    print("已清理")
    return 0


if __name__ == "__main__":
    sys.exit(main())
