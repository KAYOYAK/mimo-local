"""
MiMo Code 的进程管理、配置写入、API 调用和事件流。

架构：不在自己这里实现 agent 循环，而是起 `mimo serve` 后台进程，
通过它的 HTTP API 驱动。工具调用、上下文管理、权限都是 MiMo Code 原生实现。

关键发现（实测）：
  · session 的工作目录 = serve 进程启动时的 cwd。API 上改不了
    （试过 body.directory / body.path / body.worktree / 各种 header，全不生效）
  · 权限必须在配置里预授权，否则 permission.asked 会一直等，把请求卡死
    实测：不预授权 → 卡 100 秒；预授权 → 39 秒正常完成
  · 流式靠 GET /event 的 SSE，事件类型：
      message.part.updated  part 的完整对象（不是增量）
      session.status        busy / idle
      permission.asked      理论上预授权后不会出现，但要兜底
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import config


def _no_window() -> dict:
    if sys.platform == "win32":
        return {
            "creationflags": subprocess.CREATE_NO_WINDOW,
            "startupinfo": subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW),
        }
    return {}


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


# ================= 配置 =================

CONFIG_TEMPLATE = """{
  "$schema": "https://mimo.xiaomi.com/mimocode/config.json",
  "model": "local/mimo-9b",
  "instructions": %(instructions)s,
  "permission": {
    "bash": "allow",
    "edit": "allow",
    "read": "allow",
    "list": "allow",
    "glob": "allow",
    "grep": "allow",
    "external_directory": "allow",
    "web": "deny",
    "webfetch": "deny",
    "question": "deny"
  },
  "provider": {
    "local": {
      "name": "本机",
      "npm": "@ai-sdk/openai-compatible",
      "only_configured_models": true,
      "models": {
        "mimo-9b": {
          "name": "MiMo-V2.6-Distill-Qwen-9B",
          "tool_call": true,
          "limit": { "context": %(ctx)d, "output": 4096 }
        }
      },
      "options": {
        "baseURL": "http://127.0.0.1:%(port)d/v1",
        "apiKey": "none"
      }
    }
  }
}
"""


def _write_agents_md(cfg_home: Path) -> Path:
    """生成 OCR 工具说明，路径按**当前安装位置**填。

    为什么不能直接用仓库里那份静态 AGENTS.md：
    它里面写的是 `D:\\mimo-desktop\\tools\\ocr.py`。装到
    `%LOCALAPPDATA%\\MiMoLocal` 之后，模型会去调一个不存在的路径。

    打包成 exe 后还有第二层问题：tools/ocr.py 进了压缩包，磁盘上没有这个
    脚本了。所以 frozen 模式下改用 exe 自己的 `--ocr` 子命令。
    """
    frozen = getattr(sys, "frozen", False)
    if frozen:
        cmd = f'"{sys.executable}" --ocr'
        read_hint = ("调用成功后，**结果已经写到图片旁边的同名 `.md` 文件**"
                     "（`invoice.jpg` → `invoice.md`），用 read 工具读那个文件。\n"
                     "程序是 windowed 的、没有 stdout，所以不要指望从命令输出里拿到内容。")
    else:
        cmd = f'python "{config.ROOT / "tools" / "ocr.py"}"'
        read_hint = ("命令会把 Markdown 打到 stdout，同时也写一份到图片旁边的同名 "
                     "`.md` 文件；哪个方便用哪个。")

    body = f"""# 本地工具

这个环境跑在纯本地的模型上，除了读写文件、执行命令，还多一个**本地图片识别**工具。

## ocr —— 把图片转成 Markdown

```bash
{cmd} <图片路径>
```

可选 `--task`：

| 参数 | 用途 |
|---|---|
| （默认 `doc`） | 整页文档 → Markdown，表格转 OTSL、公式转 LaTeX |
| `--task text` | 只提取文字 |
| `--task table` | 表格 → OTSL |
| `--task formula` | 公式 → LaTeX |

图片路径可以是相对的（相对当前工作目录）。

{read_hint}

输出的就是 Markdown 正文，不需要再解析。

### 什么时候用

用户提到以下内容时，先确认文件存在，然后调用这个工具：

- 「这张图里写了什么」「看看这个截图」
- 发票、收据、扫描件、书页
- 「把这张表格转成 Markdown」
- 「这个公式是什么」
- 任何**以图片形式存在的文档**

### 注意

- 第一次调用会启动 OCR 引擎（约 4 秒），之后就快了
- 一次只处理一张图，批量就循环调用
- 识别结果是**原文转录**，不要自己编造内容
- 这个工具**只能看图片**
"""
    target = cfg_home / "AGENTS-ocr.md"
    target.write_text(body, encoding="utf-8")
    return target


def write_config(ctx: int = None, port: int = None) -> Path:
    """写 MiMo Code 的配置。每次启动都重写，保证权限和模型参数是最新的。

    返回配置目录（要作为 XDG_CONFIG_HOME 传给子进程）。
    """
    ctx = ctx or config.CHAT_CTX
    port = port or config.CHAT_PORT

    cfg_home = config.MIMO_AGENT_DIR / "config"
    cfg_dir = cfg_home / "mimocode"
    cfg_dir.mkdir(parents=True, exist_ok=True)

    # instructions 指向**生成出来**的 OCR 说明（路径按当前安装位置填），
    # 不是仓库里那份写死 D:\mimo-desktop 的静态文件。
    try:
        instr_file = _write_agents_md(cfg_home)
        instructions = json.dumps([str(instr_file)])
    except Exception:
        instructions = "[]"

    body = CONFIG_TEMPLATE % {
        "instructions": instructions,
        "ctx": ctx,
        "port": port,
    }
    target = cfg_dir / "mimocode.jsonc"
    target.write_text(body, encoding="utf-8")
    return cfg_home


# ================= 事件 =================

@dataclass
class Event:
    type: str
    session_id: str
    part: dict | None = None
    status: str = ""
    raw: dict | None = None


class EventStream:
    """订阅 /event 的 SSE，解析后回调。

    必须在独立线程里 run()。stop() 会断开连接。

    断流不再是静默的：旧实现最后是 `except Exception: pass`，SSE 一断界面
    就停止刷新而 send_message 还在阻塞 → 用户看到永久「思考中」。
    现在异常会通过 on_error 回调报出去，界面可以提示并用 POST 返回值对账。
    """

    def __init__(self, base_url: str, on_event: Callable[[Event], None],
                 on_error: Callable[[str], None] | None = None):
        self.base_url = base_url
        self.on_event = on_event
        self.on_error = on_error
        self._stop = threading.Event()
        self._resp = None
        self.last_event = 0.0        # 最后一次收到事件的时刻（time.time()）

    def run(self):
        url = f"{self.base_url}/event"
        try:
            self._resp = urllib.request.urlopen(url, timeout=3600)
            for raw in self._resp:
                if self._stop.is_set():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                self.last_event = time.time()
                ev = self._parse(obj)
                if ev:
                    try:
                        self.on_event(ev)
                    except Exception:
                        pass
        except Exception as e:
            # 主动 stop() 会走到这里，不算错误
            if not self._stop.is_set() and self.on_error:
                try:
                    self.on_error(f"事件流断开：{e}")
                except Exception:
                    pass

    def _parse(self, obj: dict) -> Event | None:
        t = obj.get("type", "")
        props = obj.get("properties") or {}
        sid = props.get("sessionID", "")
        if t == "message.part.updated":
            return Event(t, sid, part=props.get("part"))
        if t == "session.status":
            st = props.get("status") or {}
            return Event(t, sid, status=st.get("type", ""))
        if t == "permission.asked":
            return Event(t, sid, raw=props)
        if t == "session.updated":
            return Event(t, sid, raw=props)
        # 其余类型（heartbeat 等）忽略，省得界面被噪音刷
        return None

    def start(self) -> threading.Thread:
        th = threading.Thread(target=self.run, daemon=True)
        th.start()
        return th

    def stop(self):
        self._stop.set()
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:
            pass


# ================= 服务进程 =================

class AgentServer:
    """管理 `mimo serve` 进程。"""

    def __init__(self, port: int = 4096):
        self.port = port
        self.proc: subprocess.Popen | None = None
        self._log = None
        self.events: EventStream | None = None
        self.on_event: Callable[[Event], None] | None = None
        self.on_event_error: Callable[[str], None] | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def env(self) -> dict:
        import os
        a = config.MIMO_AGENT_DIR
        e = dict(os.environ)
        e["XDG_CONFIG_HOME"] = str(a / "config")
        e["XDG_DATA_HOME"] = str(a / "data")
        e["XDG_CACHE_HOME"] = str(a / "cache")
        e["XDG_STATE_HOME"] = str(a / "state")
        return e

    def start(self, on_phase=None, workdir: Path | None = None) -> bool:
        if self.running and _port_open(self.port):
            return True

        if _port_open(self.port):
            raise RuntimeError(
                f"端口 {self.port} 已被占用。\n"
                f"如果上一轮程序异常退出，用任务管理器结束 mimo.exe 再试。"
            )

        cwd = Path(workdir) if workdir else config.ROOT
        if not cwd.is_dir():
            raise RuntimeError(f"工作目录不存在：{cwd}")

        # 先写配置（含权限预授权）
        write_config()

        log_path = config.LOGS / "agent-server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(log_path, "ab")

        if on_phase:
            on_phase("正在启动 MiMo Code…")

        self.proc = subprocess.Popen(
            [str(config.MIMO_EXE), "serve", "--port", str(self.port)],
            cwd=str(cwd),
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=self._log,
            **_no_window(),
        )
        # 写 pid：程序异常退出时 mimo serve 不会跟着退，会一直占着端口，
        # 下次启动就撞「端口 4096 已被占用」。有 pid 才能收尸。
        try:
            from core.engine import write_pid
            write_pid(config.AGENT_PID_FILE, self.proc.pid)
        except Exception:
            pass

        deadline = time.time() + 90
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.stop()
                raise RuntimeError(f"MiMo Code 启动即退出。日志：{log_path}")
            if _port_open(self.port):
                try:
                    self.api("GET", "/config", timeout=3)
                    break
                except Exception:
                    pass
            time.sleep(0.4)
        else:
            self.stop()
            raise RuntimeError(f"MiMo Code 90 秒内没就绪。日志：{log_path}")

        # 订阅事件流
        if self.on_event:
            self.events = EventStream(self.base, self.on_event, self.on_event_error)
            self.events.start()

        return True

    def stop(self) -> None:
        if self.events:
            self.events.stop()
            self.events = None
        if self.proc is not None:
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                        self.proc.wait(timeout=5)
            except Exception:
                pass
            self.proc = None
        if self._log is not None:
            try:
                self._log.close()
            except Exception:
                pass
            self._log = None
        # 正常退出清掉 pid，避免下次启动误杀同号进程
        try:
            config.AGENT_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    # ---------- HTTP ----------

    def api(self, method: str, path: str, body=None, timeout: float = 30):
        url = self.base + path
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw.decode("utf-8", "replace")) if raw else None
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            raise RuntimeError(f"{method} {path} → HTTP {e.code}：{detail[:300]}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"连不上 MiMo Code：{e.reason}") from e

    # ---------- 权限兜底 ----------

    def answer_pending_permissions(self) -> int:
        """把所有待处理权限请求都放行。

        配置里已经预授权了，这里只是兜底 —— 万一有没覆盖到的权限类型，
        不让请求卡死。
        """
        try:
            pend = self.api("GET", "/permission", timeout=5) or []
        except Exception:
            return 0
        n = 0
        for p in pend:
            pid = p.get("id")
            if not pid:
                continue
            # 试几种可能的应答路径（不同版本 API 不一样）
            for path, body in (
                (f"/permission/{pid}", {"response": "always"}),
                (f"/permission/{pid}", {"reply": "always"}),
                (f"/permission/{pid}/reply", {"response": "always"}),
            ):
                try:
                    self.api("POST", path, body, timeout=5)
                    n += 1
                    break
                except Exception:
                    continue
        return n


# ---------- 会话操作 ----------

def create_session(server: AgentServer, directory: Path) -> str:
    obj = server.api("POST", "/session", {"directory": str(directory)}, timeout=15)
    return obj["id"]


def list_sessions(server: AgentServer, directory: Path | None = None) -> list:
    """列出会话。返回原始的会话对象列表（含 id / title / time）。

    MiMo Code 会自己给会话生成标题（titleSource = "generated" 时是真标题，
    "fallback" 时是「Untitled」之类），界面直接用它。
    """
    try:
        items = server.api("GET", "/session", timeout=20) or []
    except Exception:
        return []
    if directory is not None:
        want = str(directory).lower()
        items = [s for s in items if str(s.get("directory", "")).lower() == want]
    # 最近更新的排前面
    items.sort(key=lambda s: (s.get("time") or {}).get("updated", 0), reverse=True)
    return items


def get_messages(server: AgentServer, session_id: str) -> list:
    return server.api("GET", f"/session/{session_id}/message", timeout=30) or []


def send_message(server: AgentServer, session_id: str, text: str, timeout: float = 900) -> dict:
    body = {"parts": [{"type": "text", "text": text}]}
    return server.api("POST", f"/session/{session_id}/message", body, timeout=timeout)


def abort_session(server: AgentServer, session_id: str) -> bool:
    """中断当前回合。

    实测 `POST /session/{id}/abort` 返回 200（`/cancel`、`/interrupt`、
    `/stop` 都是 503，只有 abort 是真的）。
    9B 模型一个任务经常调十几次工具、跑几十秒，没有中断手段很难用。
    """
    try:
        server.api("POST", f"/session/{session_id}/abort", {}, timeout=10)
        return True
    except Exception:
        return False


# ---------- 消息解析 ----------

@dataclass
class Step:
    kind: str          # "text" | "reasoning" | "tool"
    text: str = ""
    tool: str = ""
    status: str = ""
    args: str = ""
    call_id: str = ""


def parse_part(part: dict) -> Step | None:
    """把单个 part 转成可显示步骤。"""
    t = part.get("type")
    if t == "text":
        txt = (part.get("text") or "").strip()
        return Step("text", txt) if txt else None
    if t == "reasoning":
        txt = (part.get("text") or "").strip()
        return Step("reasoning", txt) if txt else None
    if t == "tool":
        st = part.get("state") or {}
        inp = st.get("input")
        args = ""
        if inp:
            try:
                args = json.dumps(inp, ensure_ascii=False)
            except Exception:
                args = str(inp)
            if len(args) > 220:
                args = args[:220] + "…"
        return Step("tool", tool=part.get("tool", "?"),
                    status=st.get("status", ""), args=args,
                    call_id=part.get("callID", ""))
    return None


def parse_message(msg: dict) -> list[Step]:
    out = []
    for p in msg.get("parts", []):
        s = parse_part(p)
        if s:
            out.append(s)
    return out
