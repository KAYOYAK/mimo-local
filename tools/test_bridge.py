"""桥的回归测试：单轮 / 多轮 / 中断。

这三个场景各自对应一个真实踩过的坑：

  single  工具调用能穿过桥（part → content_block_delta）
  multi   多轮对话记得上文（session key 里带 len(messages) 就每轮新建）
  abort   点停止能把后端真停掉（只断前端 fetch 的话 9B 还在烧 GPU）

用法：

    python tools/test_bridge.py              # 三个都跑
    python tools/test_bridge.py -s multi     # 只跑多轮
    python tools/test_bridge.py --keep       # 跑完不关引擎（调试用）

退出码 0 = 全过，1 = 有失败。

⚠ 会占用 8080 / 4096 / 8090 / 8766，并加载 9B（约 20 秒）。
   跑之前把已经开着的 MiMo Local 关掉。
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config                                          # noqa: E402
from core import agent as ag                           # noqa: E402
from core import agent_bridge as ab                    # noqa: E402

WORKDIR = Path(r"D:\projects\demo")
BRIDGE_PORT = 8766
RESULTS: list[tuple[str, bool, str]] = []


# ---------------------------------------------------------------- 基础设施

def wait_http(url: str, timeout: int = 240) -> bool:
    for _ in range(timeout):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def start_engines(with_ocr: bool = False) -> tuple:
    """起 9B + MiMo Code（+ 可选 OCR）。"""
    WORKDIR.mkdir(parents=True, exist_ok=True)

    config.LOGS.mkdir(parents=True, exist_ok=True)
    log = open(config.LOGS / "test-bridge-llama.log", "wb")
    llama = subprocess.Popen(
        [str(config.ENGINE), "-m", str(config.CHAT_MODEL),
         "--mmproj", str(config.CHAT_MMPROJ),
         "--host", "127.0.0.1", "--port", str(config.CHAT_PORT),
         "-c", "65536", "-ngl", "99", "--flash-attn", "on",
         "-ctk", "q8_0", "-ctv", "q8_0", "--reasoning-format", "deepseek"],
        stdout=subprocess.DEVNULL, stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if not wait_http(f"http://127.0.0.1:{config.CHAT_PORT}/health"):
        raise RuntimeError("9B 没起来（显存不够？看 logs/test-bridge-llama.log）")

    server = ag.AgentServer(4096)
    server.start(workdir=WORKDIR)
    ab.set_agent(server)

    ocr = None
    if with_ocr:
        from core import engine as eng
        ocr = eng.make_ocr_engine()
        ocr.start()
        if not wait_http(f"http://127.0.0.1:{config.OCR_PORT}/health", 240):
            raise RuntimeError("OCR 引擎没起来")

    bridge = ab.AgentBridge(WORKDIR, BRIDGE_PORT)
    port = bridge.start()
    if not port:
        raise RuntimeError(f"桥起不来（{BRIDGE_PORT} 被占？）")

    return llama, log, server, bridge, port, ocr


def ask(port: int, messages: list, timeout: int = 300) -> tuple[str, str]:
    """发一次 Anthropic 格式请求，返回 (正文, 思考过程)。

    工具调用走 thinking block（可折叠），所以检查工具调用要看第二个返回值 ——
    只看正文会漏掉。
    """
    body = json.dumps({
        "model": "mimo-local", "max_tokens": 512, "stream": True,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/messages", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    text: list[str] = []
    think: list[str] = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip()
            if not line.startswith("data:"):
                continue
            try:
                obj = json.loads(line[5:].strip())
            except Exception:
                continue
            if obj.get("type") != "content_block_delta":
                continue
            d = obj.get("delta") or {}
            if d.get("type") == "text_delta":
                text.append(d.get("text") or "")
            elif d.get("type") == "thinking_delta":
                think.append(d.get("thinking") or "")
    return "".join(text), "".join(think)


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'OK  ' if ok else 'FAIL'} {name}: {detail}")


# ---------------------------------------------------------------- 三个场景

def scene_single(bridge, port: int) -> None:
    """单轮：让模型读文件，工具调用要能穿过桥显示出来。

    坑：part 类型 → block 的映射写错时，工具调用会静默消失，
    回答看起来像「模型什么都不做就答完了」。
    """
    # 必须用一个「不调工具就猜不到」的任务。
    # 之前问「列出目录下有哪些文件」，模型直接从上下文答了，没调工具 ——
    # 那样的断言测不到桥的工具调用映射。
    needle = "MIMO_TOOL_OK_9917"
    text, think = ask(port, [{
        "role": "user",
        "content": f"用 bash 执行 `echo {needle}`，把命令的原始输出告诉我。",
    }])
    got = needle in text
    has_tool = "🔧" in think
    record("single/工具调用可见", has_tool, f"思考流：{think[:90]!r}")
    record("single/命令输出回来", got, "" if got else f"没看到 {needle}")


def scene_multi(bridge, port: int) -> None:
    """多轮：第 2 轮要记得第 1 轮埋的事实。

    坑：session key 里带 len(messages)，每轮都新建 session → 模型失忆。
    这个 bug 单轮测试看不出来。
    """
    mark = "7429"
    m1 = [{"role": "user", "content": f"记住这个数字：{mark}。只回复「记住了」。"}]
    r1, _ = ask(port, m1)

    m2 = m1 + [
        {"role": "assistant", "content": r1},
        {"role": "user", "content": "我刚才让你记的数字是多少？只回答数字。"},
    ]
    r2, _ = ask(port, m2)
    record("multi/第 2 轮记得上文", mark in r2, f"回答：{r2[:60]!r}")

    # 反证：换个对话（第一条不同）不该知道这个数字
    m3 = [{"role": "user", "content": "我刚才让你记的数字是多少？只回答数字。"}]
    r3, _ = ask(port, m3)
    record("multi/新对话是隔离的", mark not in r3, f"回答：{r3[:60]!r}")


def scene_abort(bridge, port: int) -> None:
    """中断：客户端断连后，后端 MiMo Code 必须变成 idle。

    坑：chat-demo 的停止只 abort 前端 fetch，后端不知道，
    9B 继续烧 GPU、工具继续跑。
    """
    task = "递归列出 D:\\projects 下所有文件，逐个读内容并总结，一个一个来，不要跳过。"
    body = json.dumps({
        "model": "mimo-local", "max_tokens": 2048, "stream": True,
        "messages": [{"role": "user", "content": task}],
    }).encode()

    s = socket.create_connection(("127.0.0.1", port), timeout=60)
    s.sendall(
        (f"POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
         f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
         f"Connection: close\r\n\r\n").encode() + body
    )

    buf = b""
    events = 0
    t0 = time.time()
    s.settimeout(120)
    try:
        while events < 4 and time.time() - t0 < 120:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            events = buf.count(b"event: ")
    except socket.timeout:
        pass
    s.close()
    print(f"      收到 {events} 个事件（{time.time() - t0:.0f}s）后断开")

    time.sleep(8)                                     # 等桥反应

    with bridge._lock:
        sids = list(bridge._sessions.values())
    if not sids:
        record("abort/后端已停", False, "桥里没有 session 记录")
        return

    idle = False
    for sid in sids:
        try:
            obj = ag_server_ref.api("GET", f"/session/{sid}", timeout=10) or {}
            busy = bool((obj.get("time") or {}).get("busy"))
            if busy is False:
                idle = True
            print(f"      {sid}: busy={busy}")
        except Exception as exc:                       # noqa: BLE001
            print(f"      {sid}: 查询失败 {exc}")
    record("abort/后端已停", idle, "" if idle else "还在 busy")


def scene_ocr(bridge, port: int) -> None:
    """图片：base64 → 桥落临时文件 → OCR → 拼进消息 → 模型基于全文回答。

    坑：桥收到 image block 却没跑 OCR（或跑完没拼进消息）时，模型会说
    「没有图片」，用户以为图片上传失败。
    """
    import base64

    img = ROOT / "docs" / "测试图.jpg"
    if not img.is_file():
        record("ocr/测试图存在", False, f"缺 {img}")
        return
    b64 = base64.b64encode(img.read_bytes()).decode()

    text, think = ask(port, [{
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": "这张图里是什么？总共多少钱？"},
        ],
    }], timeout=420)

    # 收据上有这些确定字符串。模型答不上来就说明 OCR 没进上下文。
    hits = [k for k in ("MR. D.I.Y.", "D.I.Y", "5.90", "CLOTH PEG") if k in text]
    record("ocr/识别结果进了上下文", bool(hits), f"命中 {hits}，正文：{text[:110]!r}")


# 中断场景要查后端状态，得拿到 AgentServer —— 由 main 塞进来
ag_server_ref = None


# ---------------------------------------------------------------- 入口

def main() -> int:
    global ag_server_ref

    ap = argparse.ArgumentParser(description="桥的回归测试")
    ap.add_argument("-s", "--scene", default="all",
                    choices=["all", "single", "multi", "ocr", "abort"])
    ap.add_argument("--keep", action="store_true", help="跑完不关引擎")
    args = ap.parse_args()

    for p in (config.CHAT_PORT, 4096, BRIDGE_PORT):
        try:
            with socket.create_connection(("127.0.0.1", p), timeout=1):
                print(f"端口 {p} 已被占用 —— 先把开着的 MiMo Local 关掉再跑。")
                return 1
        except OSError:
            pass

    want_ocr = args.scene in ("all", "ocr")
    print(f"起引擎（9B + MiMo Code + 桥{' + OCR' if want_ocr else ''}）…")
    llama, log, server, bridge, port, ocr = start_engines(with_ocr=want_ocr)
    ag_server_ref = server
    print(f"  ✓ 桥 {port}\n")

    scenes = {
        "single": ("单轮：工具调用", scene_single),
        "multi": ("多轮：记得上文", scene_multi),
        "ocr": ("图片：OCR 进上下文", scene_ocr),
        "abort": ("中断：后端真停", scene_abort),
    }
    todo = list(scenes) if args.scene == "all" else [args.scene]

    try:
        for key in todo:
            label, fn = scenes[key]
            print(f"[{label}]")
            try:
                fn(bridge, port)
            except Exception as exc:                   # noqa: BLE001
                record(f"{key}/异常", False, str(exc))
            print()
    finally:
        if not args.keep:
            if ocr is not None:
                try:
                    ocr.stop()
                except Exception:                      # noqa: BLE001
                    pass
            bridge.stop()
            server.stop()
            llama.terminate()
            try:
                llama.wait(timeout=10)
            except Exception:
                llama.kill()
            log.close()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 60)
    print(f"  {passed}/{total} 通过")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"    FAIL {name}: {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
