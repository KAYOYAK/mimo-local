"""
引擎管理：启动 / 就绪检测 / 停止 llama-server。

两个引擎（聊天 8080、文档识别 8090）共用这一套，只是参数不同。

关键设计：
  · 用 subprocess 起独立进程，不用线程 —— 进程崩了不会拖着 UI
  · 退出时必须 kill，否则每次用都会留下僵尸 llama-server
  · Windows 上进程树要一起杀（llama-server 自己没有子进程，但保险）
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import config


@dataclass
class EngineSpec:
    """一个引擎实例的完整描述。"""
    name: str
    exe: Path
    port: int
    args: list[str] = field(default_factory=list)
    log_path: Path | None = None


def _no_window_kwargs() -> dict:
    """Windows 上别弹黑窗口。"""
    if sys.platform == "win32":
        return {
            "creationflags": subprocess.CREATE_NO_WINDOW,
            "startupinfo": subprocess.STARTUPINFO(
                dwFlags=subprocess.STARTF_USESHOWWINDOW
            ),
        }
    return {}


class Engine:
    """管理一个 llama-server 进程的生命周期。"""

    def __init__(self, spec: EngineSpec):
        self.spec = spec
        self.proc: subprocess.Popen | None = None
        self._log_fh = None

    # ---------- 状态 ----------

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.spec.port}"

    # ---------- 启动 ----------

    def start(self, on_phase: Callable[[str], None] | None = None) -> bool:
        """启动并等就绪。返回是否成功。

        on_phase 会收到阶段文字，用来更新界面上的进度提示。
        """
        if self.running:
            return True

        spec = self.spec
        if not spec.exe.exists():
            raise FileNotFoundError(f"引擎不存在：{spec.exe}")

        # 端口被占：先看是不是上次没关掉的自己人
        if port_in_use(spec.port):
            if self._is_our_server(spec.port):
                raise RuntimeError(
                    f"端口 {spec.port} 上已经有本程序的引擎在跑了。\n"
                    f"如果你没看到它，说明上次异常退出留了残留进程——"
                    f"用任务管理器结束 llama-server.exe 再试。"
                )
            raise RuntimeError(
                f"端口 {spec.port} 被别的程序占用。\n"
                f"换个端口，或先关掉占用的程序。"
            )

        spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(spec.log_path, "ab")
        cmd = [str(spec.exe), *spec.args]

        if on_phase:
            on_phase("正在启动引擎…")

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=self._log_fh,
            cwd=str(spec.exe.parent),
            **_no_window_kwargs(),
        )

        # 轮询 /health。模型加载要几十秒，期间引擎不打日志也没响应。
        deadline = time.time() + 180
        last_beat = 0.0
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.stop()
                raise RuntimeError(
                    f"引擎启动后立刻退出了。\n日志：{spec.log_path}\n"
                    + self.tail_log(15)
                )
            if health_ok(spec.port):
                if on_phase:
                    on_phase("引擎就绪")
                return True
            now = time.time()
            if on_phase and now - last_beat > 1.0:
                last_beat = now
                elapsed = int(now - (deadline - 180))
                on_phase(f"正在加载模型… 已等 {elapsed} 秒")
            time.sleep(0.4)

        self.stop()
        raise RuntimeError(
            f"引擎 180 秒内没有就绪（模型太大或盘太慢）。\n日志：{spec.log_path}"
        )

    # ---------- 停止 ----------

    def stop(self) -> None:
        """停止引擎。幂等，可重复调用。"""
        if self.proc is not None:
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        # 不听话就硬杀 —— 宁可留不下数据，也不要留僵尸进程
                        self.proc.kill()
                        self.proc.wait(timeout=5)
            except Exception:
                pass
            self.proc = None
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

    # ---------- 调试 ----------

    def tail_log(self, n: int = 15) -> str:
        p = self.spec.log_path
        if not p or not p.exists():
            return ""
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-n:])
        except Exception:
            return ""

    @staticmethod
    def _is_our_server(port: int) -> bool:
        """端口上的服务是不是 llama-server（别人也可能是 llama-server，但至少能区分）。"""
        try:
            import urllib.request
            import json
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/props", timeout=2) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
                return "model_path" in data
        except Exception:
            return False


# ---------- 无依赖的小工具 ----------

def port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def health_ok(port: int) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


# ---------- 两个引擎的工厂 ----------

def make_chat_engine(log_dir: Path | None = None) -> Engine:
    """聊天引擎：MiMo-9B + 视觉投影。

    参数移植自 U 盘项目（launcher.ps1 的 Build-Args），关键几项：
      --flash-attn on + KV 量化   省 716 MiB 显存（实测）
      --reasoning-format deepseek  thinking 输出标准化
      完整采样参数                  长代码输出少复读
      --fit on                    装不下时自动减层，而不是直接 OOM
    """
    from core import hardware

    plan = hardware.plan_chat()
    threads = config.CHAT_THREADS or hardware.get_cpu_cores()

    args = [
        "-m", str(config.CHAT_MODEL),
        "--host", "127.0.0.1",
        "--port", str(config.CHAT_PORT),
        "--ctx-size", str(plan.ctx),
        "--threads", str(threads),
        "--jinja",
        "--reasoning-format", "deepseek",
        "--temp", str(config.CHAT_TEMP),
        "--top-p", str(config.CHAT_TOP_P),
        "--top-k", str(config.CHAT_TOP_K),
        "--min-p", str(config.CHAT_MIN_P),
        "--repeat-penalty", str(config.CHAT_REPEAT_PENALTY),
        "--n-predict", str(config.CHAT_N_PREDICT),
    ]

    # 显存装不下时让引擎自己决定放几层；装得下就全卸载
    if plan.auto_fit:
        args += ["--fit", "on"]
    else:
        args += ["--n-gpu-layers", str(plan.ngl)]

    # flash-attn 和 KV 量化都靠显卡。纯 CPU 跑时没有后端，别传这两项。
    args += [
        "--flash-attn", "on",
        "--cache-type-k", plan.kv_type,
        "--cache-type-v", plan.kv_type,
    ]

    # 有投影文件就开视觉能力，没有就显式关掉
    # （引擎在"没给投影文件"时会自己去网上下载，必须堵住）
    if config.CHAT_MMPROJ.exists():
        args += ["--mmproj", str(config.CHAT_MMPROJ)]
    else:
        args += ["--no-mmproj"]

    log = (log_dir or config.LOGS) / "engine-chat.log"
    return Engine(EngineSpec("chat", config.ENGINE, config.CHAT_PORT, args, log))


def make_ocr_engine(log_dir: Path | None = None) -> Engine:
    """OCR 引擎：Xiaomi-OCR-0，贪心解码。用完即关。"""
    args = [
        "-m", str(config.OCR_MODEL),
        "--mmproj", str(config.OCR_MMPROJ),
        "--host", "127.0.0.1",
        "--port", str(config.OCR_PORT),
        "--ctx-size", str(config.OCR_CTX),
        "--n-gpu-layers", "99",
        "--jinja",
        "--no-webui",
        "--temp", "0",
        "--top-k", "1",
    ]
    log = (log_dir or config.LOGS) / "engine-ocr.log"
    return Engine(EngineSpec("ocr", config.ENGINE, config.OCR_PORT, args, log))
