"""录制 chat-demo（MiMo Local 新前端）的对话过程，合成 GIF/MP4。

用多帧截图 + Pillow 合成，不依赖 Playwright 的 ffmpeg。
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
DOCS = Path(r"D:\mimo-desktop\docs")
PORT = 8099
LLAMA_PORT = 8080
W, H = 1280, 800

QUESTION = "用 Python 写一个判断回文的函数。"
frames: list = []


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


def main() -> int:
    print("起 llama-server…")
    log = open(Path(r"D:\mimo-desktop\logs\gif-engine.log"), "wb")
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
            page = b.new_page(viewport={"width": W, "height": H},
                              device_scale_factor=1, color_scheme="dark")
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle",
                      timeout=60000)
            page.wait_for_timeout(2500)

            def grab(n=1):
                for _ in range(n):
                    try:
                        frames.append(page.screenshot(type="png"))
                    except Exception:
                        pass

            grab(5)                                    # 欢迎页停留

            ta = page.query_selector("textarea")
            if not ta:
                print("找不到输入框", file=sys.stderr)
                return 1
            ta.click()
            # 逐字输入
            for i, ch in enumerate(QUESTION):
                ta.type(ch, delay=0)
                if i % 2 == 0:
                    grab(1)
                page.wait_for_timeout(55)
            grab(3)

            ta.press("Enter")
            t0 = time.time()
            last, stable = "", 0
            while time.time() - t0 < 240:
                grab(1)
                page.wait_for_timeout(400)
                try:
                    cur = page.inner_text("body")
                except Exception:
                    continue
                if cur == last and len(cur) > 200:
                    stable += 1
                    if stable >= 6:
                        break
                else:
                    stable = 0
                    last = cur
            grab(8)                                    # 结尾停留

            b.close()
        print(f"  共 {len(frames)} 帧")

        import io

        import numpy as np
        from PIL import Image

        pils = [Image.open(io.BytesIO(f)).convert("RGB") for f in frames]

        # GIF：960 宽 + 128 色
        gw = 960
        gh = int(gw * H / W)
        small = [im.resize((gw, gh), Image.LANCZOS) for im in pils]
        quant = [im.convert("P", palette=Image.ADAPTIVE, colors=128) for im in small]
        gif = DOCS / "demo-chat.gif"
        quant[0].save(str(gif), save_all=True, append_images=quant[1:],
                      duration=90, loop=0, optimize=True, disposal=2)
        print(f"  ✓ {gif.name}  {gif.stat().st_size / 1024:.0f} KB  {gw}x{gh}")

        # MP4
        even_w = W - (W % 2)
        even_h = H - (H % 2)
        crop = [im.crop((0, 0, even_w, even_h)) for im in pils]
        import imageio.v2 as iio
        mp4 = DOCS / "demo-chat.mp4"
        writer = iio.get_writer(str(mp4), fps=24, codec="libx264", quality=8,
                                pixelformat="yuv420p", macro_block_size=1)
        for im in crop:
            arr = np.asarray(im)
            writer.append_data(arr)
            writer.append_data(arr)
        writer.close()
        print(f"  ✓ {mp4.name}  {mp4.stat().st_size / 1024:.0f} KB")
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
