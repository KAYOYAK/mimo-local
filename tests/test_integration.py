"""
端到端集成测试：不起 GUI，直接验 UI 背后的完整链路。

覆盖：
  1. 起聊天引擎（llama.cpp + MiMo-9B + mmproj，64K）
  2. 起 mimo serve（agent 后端）
  3. 创建会话 → 发消息 → 触发工具调用 → 解析 parts
  4. 全部清理

跑法：python test_integration.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from core import agent as ag
from core import client
from core import engine as eng


def step(n, title):
    print(f"\n{'─' * 62}\n[{n}] {title}\n{'─' * 62}")


def main() -> int:
    print("=" * 62)
    print("  MiMo Local · 端到端集成测试")
    print("=" * 62)

    # 准备测试项目目录
    proj = config.ROOT / "testproj"
    proj.mkdir(exist_ok=True)
    (proj / "note.txt").write_text(
        "项目名：星河计划\n交付日期：2026-12-01\n负责人：李四\n预算：48 万元\n",
        encoding="utf-8",
    )
    print(f"测试目录：{proj}")

    chat = None
    server = None
    try:
        # ---------- 1. 聊天引擎 ----------
        step(1, "启动聊天引擎（llama.cpp / MiMo-9B / 64K）")
        chat = eng.make_chat_engine()
        t0 = time.time()
        chat.start(on_phase=lambda s: print(f"    … {s}"))
        print(f"    ✓ 就绪 {time.time() - t0:.1f}s")

        ctx = client.get_context_size(config.CHAT_PORT)
        print(f"    上下文 = {ctx}")

        # ---------- 2. agent ----------
        step(2, "启动 mimo serve")
        server = ag.AgentServer(config.AGENT_PORT)
        server.start(on_phase=lambda s: print(f"    … {s}"))
        print(f"    ✓ 就绪 {server.base}")

        cfg = server.api("GET", "/config")
        print(f"    模型 = {cfg.get('model')}")

        # ---------- 3. 会话 + 消息 ----------
        step(3, "创建会话并让它读文件（触发工具调用）")
        sid = ag.create_session(server, proj)
        print(f"    会话 {sid}")

        t1 = time.time()
        ag.send_message(server, sid, "读一下 note.txt，用一句话回答：这个项目的交付日期是哪天？")
        print(f"    ✓ 完成 {time.time() - t1:.1f}s")

        # ---------- 4. 解析 parts ----------
        step(4, "解析消息链")
        msgs = ag.get_messages(server, sid)
        assistants = [m for m in msgs if m.get("info", {}).get("role") == "assistant"]
        print(f"    assistant 消息 {len(assistants)} 条")
        for i, m in enumerate(assistants, 1):
            steps = ag.parse_message(m)
            summary = []
            for s in steps:
                if s.kind == "tool":
                    summary.append(f"tool:{s.tool}/{s.status}")
                elif s.kind == "reasoning":
                    summary.append("reasoning")
                else:
                    summary.append(f"text({len(s.text)}字)")
            print(f"    [{i}] {', '.join(summary) if summary else '(空)'}")

        tools, final = ag.summarize_steps(
            [s for m in assistants for s in ag.parse_message(m)]
        )
        print(f"\n    工具链：{tools or '(无)'}")
        print(f"    最终回答：{final.strip()[:200]}")

    except Exception as e:
        print(f"\n✗ 失败：{e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        step(5, "清理")
        if server:
            server.stop()
            print("    ✓ mimo serve 已停")
        if chat:
            chat.stop()
            print("    ✓ 引擎已停")
        left = [p for p in ("mimo", "llama-server")
                if __import__("subprocess").run(
                    ["tasklist", "/FI", f"IMAGENAME eq {p}.exe"],
                    capture_output=True).stdout.decode("utf-8", "replace").lower().count(p) > 0]
        print(f"    残留进程：{left or '无'}")

    print("\n" + "=" * 62)
    print("  集成测试通过")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
