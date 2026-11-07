"""
OCR 工具调用测试：验证模型能不能自己发现并使用 ocr.py。

流程：
  1. 起两个引擎（聊天 8080 + OCR 8090）
  2. 起 mimo serve（带 instructions 指向 AGENTS.md）
  3. 往项目目录放一张图片
  4. 问模型「这张图里写了什么」
  5. 看它有没有调 ocr.py，有没有拿到正确结果
"""
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from core import agent as ag
from core import engine as eng


def main() -> int:
    proj = config.ROOT / "testproj"
    proj.mkdir(exist_ok=True)

    # 准备一张真实的收据图片
    src = Path(r"D:\tmp\v1120-verify\receipt.jpg")
    if not src.exists():
        print(f"✗ 测试图片不存在：{src}")
        return 1
    dst = proj / "invoice.jpg"
    shutil.copy2(src, dst)
    print(f"测试图片：{dst}")

    chat = ocr = server = None
    try:
        print("\n[1] 启动聊天引擎")
        chat = eng.make_chat_engine()
        chat.start(on_phase=lambda s: print(f"    … {s}"))

        print("\n[2] 启动 OCR 引擎（8090）")
        ocr = eng.make_ocr_engine()
        ocr.start(on_phase=lambda s: print(f"    … {s}"))

        print("\n[3] 启动 mimo serve")
        server = ag.AgentServer(config.AGENT_PORT)
        server.start(on_phase=lambda s: print(f"    … {s}"))

        print("\n[4] 创建会话")
        sid = ag.create_session(server, proj)
        print(f"    {sid}")

        print("\n[5] 问：这张图里写了什么")
        t0 = time.time()
        ag.send_message(
            server, sid,
            "看看 invoice.jpg 这张图，告诉我这是哪家店的收据、总金额多少。",
            timeout=900,
        )
        print(f"    完成，用时 {time.time() - t0:.0f} 秒")

        print("\n[6] 分析工具调用")
        msgs = ag.get_messages(server, sid)
        assistants = [m for m in msgs if m.get("info", {}).get("role") == "assistant"]
        used_ocr = False
        final_text = ""
        for m in assistants:
            for s in ag.parse_message(m):
                if s.kind == "tool":
                    mark = ""
                    if "ocr" in (s.args or "").lower() or "ocr.py" in (s.args or "").lower():
                        used_ocr = True
                        mark = "  ★ 用了 OCR！"
                    print(f"    tool {s.tool} [{s.status}] {s.args[:120]}{mark}")
                elif s.kind == "text":
                    final_text = s.text

        print("\n" + "=" * 60)
        print(f"  是否调用了 ocr.py：{'✓ 是' if used_ocr else '✗ 否'}")
        print(f"  最终回答：{final_text.strip()[:300]}")
        print("=" * 60)
        return 0 if used_ocr else 2

    except Exception as e:
        print(f"\n✗ 失败：{e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        for name, obj in (("mimo serve", server), ("OCR 引擎", ocr), ("聊天引擎", chat)):
            if obj:
                obj.stop()
                print(f"    ✓ {name} 已停")


if __name__ == "__main__":
    sys.exit(main())
