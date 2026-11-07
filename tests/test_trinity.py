"""
验证主线：三个能力是否真的能「同时在线」。

目标：
    8080  MiMo V2.6 9B   （llama.cpp）
    8090  Xiaomi-OCR-0   （llama.cpp）
    4096  MiMo Code      （mimo serve）

测的是三件事：
    1. 三个能不能同时起来
    2. 显存够不够（这是硬约束）
    3. 起来之后，模型能不能真的调用 OCR 工具
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from core import agent as ag
from core import client
from core import engine as eng


def vram() -> tuple[int, int]:
    r = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10)
    try:
        used, total = r.stdout.strip().split(",")
        return int(used), int(total)
    except Exception:
        return -1, -1


def main() -> int:
    proj = config.ROOT / "testproj"
    proj.mkdir(exist_ok=True)
    (proj / "账单.txt").write_text(
        "店铺：小林便利店\n日期：2026-11-07\n商品：牛奶 x2  12.00\n合计：12.00 元\n",
        encoding="utf-8")

    # 准备一张图，让模型真正触发 OCR
    img_src = Path(r"D:\tmp\v1120-verify\receipt.jpg")
    img_dst = proj / "receipt.jpg"
    if img_src.exists() and not img_dst.exists():
        import shutil
        shutil.copy2(img_src, img_dst)

    chat = ocr = server = None
    try:
        base_used, total = vram()
        print(f"启动前：显存 {base_used} / {total} MiB\n")

        print("[1] MiMo 9B（8080）")
        chat = eng.make_chat_engine()
        chat.start(on_phase=lambda s: print(f"    … {s}"))
        u1, _ = vram()
        print(f"    ✓ 已用 {u1} MiB（+{u1 - base_used}）\n")

        print("[2] Xiaomi-OCR-0（8090）")
        ocr = eng.make_ocr_engine()
        ocr.start(on_phase=lambda s: print(f"    … {s}"))
        u2, _ = vram()
        print(f"    ✓ 已用 {u2} MiB（+{u2 - u1}）\n")

        print("[3] MiMo Code（4096）")
        server = ag.AgentServer(config.AGENT_PORT)
        server.start(workdir=proj)
        u3, _ = vram()
        print(f"    ✓ 已用 {u3} MiB\n")

        print("=" * 62)
        print(f"  三个同时在线：{u3} / {total} MiB（余 {total - u3} MiB）")
        print("=" * 62)

        # 验证：模型能不能自己调 OCR
        print("\n[4] 验证：让它读一张图片收据")
        sid = ag.create_session(server, proj)
        t0 = time.time()
        ag.send_message(server, sid,
                        "看看 receipt.jpg 这张图，告诉我总金额是多少。只回答数字。",
                        timeout=300)
        print(f"    完成 {time.time() - t0:.0f}s")

        msgs = ag.get_messages(server, sid)
        used_ocr = False
        final = ""
        for m in msgs:
            for s in ag.parse_message(m):
                if s.kind == "tool":
                    if "ocr" in (s.args or "").lower():
                        used_ocr = True
                        print(f"    ✓ 调用了 OCR: {s.tool} [{s.status}]")
                elif s.kind == "text":
                    final = s.text
        print(f"    回答：{final.strip()[:100]}")
        print(f"\n    OCR 工具被调用：{'✓ 是' if used_ocr else '✗ 否'}")

    except Exception as e:
        print(f"\n✗ 失败：{e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        print("\n[5] 清理")
        for n, o in (("MiMo Code", server), ("OCR", ocr), ("9B", chat)):
            if o:
                o.stop()
                print(f"    ✓ {n} 已停")

    return 0


if __name__ == "__main__":
    sys.exit(main())
