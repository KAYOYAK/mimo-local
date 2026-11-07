"""
验证流式事件：订阅 /event，发消息，看事件是不是"边跑边来"。

这是之前 Python 线程订阅卡死的地方，必须单独验一遍。
用 EventStream 类（不是自己写 urllib），确保和界面用的是同一条代码路径。
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from core import agent as ag
from core import engine as eng

received = []
t_start = None


def on_event(ev):
    elapsed = time.time() - t_start
    received.append((elapsed, ev))
    if ev.type == "message.part.updated" and ev.part:
        step = ag.parse_part(ev.part)
        if step:
            kind = step.kind
            if kind == "tool":
                print(f"  [{elapsed:5.1f}s] tool {step.tool} [{step.status}]")
            elif kind == "text":
                print(f"  [{elapsed:5.1f}s] text +{len(step.text)}字")
            elif kind == "reasoning":
                print(f"  [{elapsed:5.1f}s] reasoning +{len(step.text)}字")
    elif ev.type == "session.status":
        print(f"  [{elapsed:5.1f}s] status → {ev.status}")


def main():
    global t_start
    proj = config.ROOT / "testproj"
    proj.mkdir(exist_ok=True)
    (proj / "info.txt").write_text("项目：星河\n状态：进行中\n负责人：王五\n", encoding="utf-8")

    chat = server = None
    try:
        print("[1] 起引擎")
        chat = eng.make_chat_engine()
        chat.start(on_phase=lambda s: print(f"    … {s}"))

        print("[2] 起 serve + 订阅事件")
        server = ag.AgentServer(config.AGENT_PORT)
        server.on_event = on_event
        server.start(workdir=proj)
        print(f"    serve OK，事件流已挂")

        time.sleep(1)
        print("[3] 发消息")
        t_start = time.time()
        sid = ag.create_session(server, proj)
        ag.send_message(server, sid, "读 info.txt，告诉我负责人是谁。只回答名字。", timeout=300)
        total = time.time() - t_start

        print(f"\n[4] 完成，总耗时 {total:.1f}s，收到 {len(received)} 条事件")
        if received:
            print(f"    首条事件在 {received[0][0]:.1f}s，末条在 {received[-1][0]:.1f}s")
            # 流式的关键证据：事件是分散到达的，不是最后一次性到
            span = received[-1][0] - received[0][0]
            print(f"    事件跨度 {span:.1f}s  {'✓ 是流式' if span > 3 else '⚠ 可能是一次性到的'}")

    finally:
        for n, o in (("serve", server), ("chat", chat)):
            if o:
                o.stop()
                print(f"    ✓ {n} 停")


if __name__ == "__main__":
    main()
