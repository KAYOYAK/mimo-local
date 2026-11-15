"""Anthropic Messages API → MiMo Code session API 的适配层。

## 为什么需要它

chat-demo 要的是 Anthropic 格式（`POST /v1/messages` + SSE），而 MiMo Code
提供的是 OpenCode 风格的 session API（`POST /session/{id}/message` +
`GET /event` 的 SSE）。两者不通。

不接这个桥，聊天界面就只能直连 llama-server（纯 9B），
**工具调用和 OCR 全都用不上** —— 三个引擎实际上只有一个是活的。

接上之后：

    chat-demo ──Anthropic──> 本桥(8766) ──session API──> MiMo Code(4096)
                                                          ├─> 9B (8080)
                                                          └─> OCR (8090)

## 协议映射

Anthropic 事件 ← MiMo Code part
  message_start          ← 收到第一条 part 前
  content_block_start    ← 某个 part id 第一次出现
  content_block_delta    ← 同一 part 的 text 变长（只发增量）
  content_block_stop     ← part 不再变化 / 回合结束
  message_delta + stop   ← session.status 变成 idle

part → block 的映射：
  type=text        → text block
  type=reasoning   → thinking block（chat-demo 会渲染成可折叠的“思考过程”）
  type=tool        → text block，内容是 `▸ <tool> <args>` 形式
                     （chat-demo 不渲染 tool_use，用它反而会中断回复；
                       转成文本既能看见工具调用，又不影响流式）

## 会话

chat-demo 每次请求都带完整历史。这里维护 `conversation_id → session_id` 的映射：
第一次见到某段历史时建 MiMo Code 会话，之后复用。
历史本身不再重放（MiMo Code 自己的会话里已经有了）。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import agent as ag

DEFAULT_PORT = 8766
MODEL_NAME = "mimo-local"


# ================= 事件 → Anthropic SSE =================

class _BlockTracker:
    """把「同一个 part 反复更新」压成 Anthropic 的 block 生命周期。"""

    def __init__(self, echo: str = "") -> None:
        self.open: dict[str, int] = {}        # part_id → block index
        self.text: dict[str, str] = {}        # part_id → 已发出的文本
        self.next_index = 0
        self.blocks_started = 0
        # 发出去的原文。MiMo Code 会把用户消息作为 text part 回显一次，
        # 前端已经画过用户那条了，再发一遍会出现「自己的问题被当成回答」。
        self.echo = (echo or "").strip()

    def _skip(self, part: dict) -> bool:
        ptype = part.get("type") or ""
        if ptype not in ("text", "reasoning", "tool"):
            return True
        # 系统注入的隐藏文本（memory 路径、instructions 副本）
        if part.get("synthetic"):
            return True
        if ptype in ("text", "reasoning"):
            txt = (part.get("text") or "").strip()
            if not txt:
                return True
            # 用户消息回显
            if self.echo and txt == self.echo:
                return True
            # MiMo Code 的 memory 提示块
            if txt.startswith("<system-reminder>"):
                return True
        return False

    def feed(self, part: dict) -> list[dict]:
        """返回要发的事件列表。"""
        if self._skip(part):
            return []
        pid = part.get("id") or ""
        if not pid:
            return []
        ptype = part.get("type") or ""
        events: list[dict] = []

        if ptype == "text":
            full = part.get("text") or ""
            kind = "text"
            delta_type = "text_delta"
            field = "text"
        elif ptype == "reasoning":
            full = part.get("text") or ""
            kind = "thinking"
            delta_type = "thinking_delta"
            field = "thinking"
        elif ptype == "tool":
            # 工具调用转成文本，前端才看得见。
            # 只发**最终状态**：MiMo Code 会为同一个工具连续推 pending →
            # running → completed，全发出来会把回答刷满，看着像乱码。
            tool = part.get("tool") or part.get("name") or "tool"
            state = part.get("state") or {}
            status = state.get("status") or "running"
            if status in ("pending", "running"):
                return []
            args = state.get("input") or state.get("args") or {}
            try:
                arg_s = json.dumps(args, ensure_ascii=False)
            except Exception:                      # noqa: BLE001
                arg_s = str(args)
            if len(arg_s) > 200:
                arg_s = arg_s[:200] + "…"
            mark = "✓" if status == "completed" else "✗"
            full = f"{mark} {tool} {arg_s}\n\n"
            kind = "text"
            delta_type = "text_delta"
            field = "text"
        else:
            return []

        if not full:
            return []

        if pid not in self.open:
            idx = self.next_index
            self.next_index += 1
            self.open[pid] = idx
            self.text[pid] = ""
            self.blocks_started += 1
            events.append({
                "type": "content_block_start",
                "index": idx,
                "content_block": {"type": kind, field: ""},
            })

        prev = self.text.get(pid, "")
        if full.startswith(prev):
            inc = full[len(prev):]
            if inc:
                self.text[pid] = full
                events.append({
                    "type": "content_block_delta",
                    "index": self.open[pid],
                    "delta": {"type": delta_type, field: inc},
                })
        elif full != prev:
            # part 被整体改写了（工具状态从 running 变 done），补发一次完整内容
            self.text[pid] = full
            events.append({
                "type": "content_block_delta",
                "index": self.open[pid],
                "delta": {"type": delta_type, field: "  " + full},
            })
        return events

    def close_all(self) -> list[dict]:
        return [
            {"type": "content_block_stop", "index": i}
            for i in sorted(self.open.values())
        ]


# ================= 桥本体 =================

class AgentBridge:
    """把 MiMo Code 包成 Anthropic 兼容的 HTTP 端点。"""

    def __init__(self, workdir: Path, port: int = DEFAULT_PORT) -> None:
        self.workdir = workdir
        self.port = port
        self._srv: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # conversation 指纹 → MiMo Code session_id
        self._sessions: dict[str, str] = {}

    # ---------- 生命周期 ----------

    def start(self) -> int | None:
        server = self

        class Handler(_BridgeHandler):
            bridge = server

        # 端口必须固定：chat-demo 的 API_ORIGIN 是构建时注入的，
        # 桥要是自动换端口，前端就找不到了。
        try:
            self._srv = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError:
            return None
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._srv:
            try:
                self._srv.shutdown()
                self._srv.server_close()
            except Exception:                      # noqa: BLE001
                pass
            self._srv = None

    # ---------- 会话 ----------

    def session_for(self, agent_server: ag.AgentServer, key: str) -> str:
        with self._lock:
            sid = self._sessions.get(key)
            if sid:
                return sid
        # MiMo Code 的 session cwd 由 serve 进程启动时的目录决定，
        # 这里只是把「期望目录」告诉它。优先用 agent 自己的 workdir，
        # 拿不到才退回构造时传的那个。
        workdir = getattr(agent_server, "workdir", None) or self.workdir
        sid = ag.create_session(agent_server, Path(workdir))
        with self._lock:
            self._sessions[key] = sid
        return sid


# ================= HTTP 处理 =================

class _BridgeHandler(BaseHTTPRequestHandler):
    bridge: AgentBridge

    def log_message(self, *args):                  # 别把每个请求刷进控制台
        pass

    # ---------- 工具 ----------

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "replace"))
        except Exception:                          # noqa: BLE001
            return {}

    def _sse_open(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _sse(self, event: dict) -> None:
        payload = f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        self.wfile.write(payload.encode())
        self.wfile.flush()

    # ---------- 路由 ----------

    def do_OPTIONS(self):                          # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self):                              # noqa: N802
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._json(200, {"data": [{"id": MODEL_NAME, "object": "model"}],
                             "models": [{"name": MODEL_NAME, "model": MODEL_NAME}]})
            return
        if self.path.rstrip("/") in ("/health", ""):
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": {"message": "not found", "type": "not_found_error"}})

    def do_POST(self):                             # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if path in ("/v1/messages", "/messages"):
            self._handle_message()
            return
        self._json(404, {"error": {"message": "not found", "type": "not_found_error"}})

    # ---------- 核心：一次对话回合 ----------

    def _handle_message(self) -> None:
        body = self._read_json()
        messages = body.get("messages") or []
        if not messages:
            self._json(400, {"error": {"message": "messages 为空", "type": "invalid_request"}})
            return

        # 取最后一条用户消息的文本（历史不再重放，MiMo Code 自己的会话里有）
        text = ""
        for m in reversed(messages):
            if m.get("role") != "user":
                continue
            c = m.get("content")
            if isinstance(c, str):
                text = c
            elif isinstance(c, list):
                text = "\n".join(
                    b.get("text", "") for b in c
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            break
        if not text.strip():
            self._json(400, {"error": {"message": "没有文本内容", "type": "invalid_request"}})
            return

        bridge = self.bridge
        agent_server = _grab_agent()
        if agent_server is None:
            self._json(503, {"error": {"message": "MiMo Code 没在运行", "type": "unavailable"}})
            return

        # 会话键：按历史第一条消息的指纹，让「新对话」和「继续对话」分开
        first = messages[0]
        c0 = first.get("content")
        seed = c0 if isinstance(c0, str) else json.dumps(c0, ensure_ascii=False, sort_keys=True)
        key = f"{seed[:200]}|{len(messages)}"

        try:
            sid = bridge.session_for(agent_server, key)
        except Exception as exc:                   # noqa: BLE001
            self._json(502, {"error": {"message": f"建会话失败：{exc}",
                                       "type": "upstream_error"}})
            return

        tracker = _BlockTracker(echo=text)
        done = threading.Event()
        parts_seen: dict[str, dict] = {}
        error: list[str] = []
        last_part_at = [0.0]

        def on_event(ev: ag.Event) -> None:
            if ev.session_id and ev.session_id != sid:
                return
            if ev.type == "message.part.updated" and ev.part:
                pid = ev.part.get("id") or ""
                if pid:
                    parts_seen[pid] = ev.part
                    last_part_at[0] = time.time()
                try:
                    for e in tracker.feed(ev.part or {}):
                        self._sse(e)
                except Exception:                  # noqa: BLE001
                    pass
            elif ev.type == "session.status" and ev.status == "idle":
                done.set()

        self._sse_open()
        self._sse({
            "type": "message_start",
            "message": {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "role": "assistant",
                "model": MODEL_NAME,
                "content": [],
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })

        stream = ag.EventStream(agent_server.base, on_event,
                                lambda msg: error.append(msg))
        t = threading.Thread(target=stream.run, daemon=True)
        t.start()
        time.sleep(0.3)                            # 等订阅建好，别漏掉第一批 part

        send_ok = False
        try:
            ag.send_message(agent_server, sid, text, timeout=900)
            send_ok = True
        except Exception as exc:                   # noqa: BLE001
            error.append(str(exc))
        finally:
            # 回合结束有三种信号，哪个先到都算：
            #   1. session.status 变 idle（最准，但实测不一定发）
            #   2. send_message 返回了，且最后一条 part 已经 8 秒没更新
            #   3. 硬超时兜底
            deadline = time.time() + 900
            while time.time() < deadline:
                if done.is_set():
                    break
                if send_ok and last_part_at[0] and time.time() - last_part_at[0] > 8:
                    break
                time.sleep(0.2)
            stream.stop()
            time.sleep(0.2)

        for e in tracker.close_all():
            try:
                self._sse(e)
            except Exception:                      # noqa: BLE001
                pass

        self._sse({
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 0},
        })
        self._sse({"type": "message_stop"})
        # SSE 收尾后必须断开：客户端（chat-demo 用 fetch 读流）要看到
        # 连接关闭才会认为回合结束，否则会一直挂着等到超时。
        try:
            self.close_connection = True
            self.wfile.flush()
        except Exception:                      # noqa: BLE001
            pass

        if error:
            # 已经开了流，只能在末端额外发一条说明
            try:
                self._sse({
                    "type": "content_block_start",
                    "index": tracker.next_index,
                    "content_block": {"type": "text", "text": ""},
                })
                self._sse({
                    "type": "content_block_delta",
                    "index": tracker.next_index,
                    "delta": {"type": "text_delta", "text": f"\n\n[桥接错误] {error[0]}"},
                })
                self._sse({"type": "content_block_stop", "index": tracker.next_index})
            except Exception:                      # noqa: BLE001
                pass


# ================= 进程内单例（ui_web 共享） =================

_agent_server: ag.AgentServer | None = None


def set_agent(server: ag.AgentServer | None) -> None:
    """由主程序在启动/停止引擎时写入。"""
    global _agent_server
    _agent_server = server


def peek_agent() -> ag.AgentServer | None:
    """给 ui_web 查询当前 agent（拿它的 workdir 用）。"""
    return _agent_server


def _grab_agent() -> ag.AgentServer | None:
    return _agent_server
