"""
诊断报告 —— 移植自 U 盘项目的 tools/反馈报告.ps1。

目的：出问题的时候，一条命令收集齐所有排查需要的信息，
     生成一份纯文本报告，直接复制出来就能定位问题。

跑法：
    python tools/report.py
    或在自检后加 --report
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from core import hardware


def _run(cmd: list[str], timeout: int = 12) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        return (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return f"（执行失败：{e}）"


def _sha256_quick(path: Path, limit_mb: int = 512) -> str:
    """大文件只算前 N MB，够用来确认文件没被换掉。"""
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            left = limit_mb * 1024 * 1024
            while left > 0:
                chunk = f.read(min(1024 * 1024, left))
                if not chunk:
                    break
                h.update(chunk)
                left -= len(chunk)
        size = path.stat().st_size
        tag = "全文" if size <= limit_mb * 1024 * 1024 else f"前{limit_mb}MB"
        return f"{h.hexdigest()}（{tag}）"
    except Exception as e:
        return f"（算不出来：{e}）"


def _gpu_info() -> list[str]:
    lines = []
    out = _run(["nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,driver_version",
                "--format=csv,noheader"])
    if out and "失败" not in out:
        for i, ln in enumerate(out.splitlines(), 1):
            lines.append(f"  GPU{i}: {ln}")
    else:
        lines.append("  nvidia-smi 不可用（非 NVIDIA 或驱动没装）")
    return lines


def _log_tail(path: Path, n: int = 20) -> list[str]:
    if not path.is_file():
        return [f"  （没有 {path.name}）"]
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = lines[-n:]
        return [f"  {ln[:160]}" for ln in tail]
    except Exception as e:
        return [f"  （读不出来：{e}）"]


def build_report(checker=None) -> str:
    now = datetime.now()
    L: list[str] = []

    def add(s=""):
        L.append(s)

    add("=" * 72)
    add("  MiMo Local · 诊断报告")
    add(f"  生成时间：{now.strftime('%Y-%m-%d %H:%M:%S')}")
    add("=" * 72)
    add()

    # ---------- 系统 ----------
    add("【系统】")
    add(f"  OS        {platform.platform()}")
    add(f"  机器名    {platform.node()}")
    add(f"  Python    {sys.version.split()[0]}")
    add(f"  运行方式  {'打包 exe' if getattr(sys, 'frozen', False) else 'Python 源码'}")
    add(f"  程序目录  {config.ROOT}")
    add()

    # ---------- 硬件 ----------
    add("【硬件】")
    add(f"  CPU       {platform.processor() or '未知'}")
    add(f"  物理核心  {hardware.get_cpu_cores()}")
    add(f"  内存      {hardware.get_ram_gb()} GB")
    add(f"  NVIDIA 显存 {hardware.get_nvidia_vram_gb()} GB（已用 {hardware.get_nvidia_used_gb()} GB）")
    for ln in _gpu_info():
        add(ln)
    add()

    # ---------- 引擎配置 ----------
    plan = hardware.plan_chat()
    add("【引擎配置（自动推导）】")
    add(f"  上下文    {plan.ctx} tokens（{plan.ctx // 1024}K）")
    add(f"  KV 精度   {plan.kv_type}")
    add(f"  卸载层数  {'自动（--fit on）' if plan.auto_fit else plan.ngl}")
    add(f"  线程      {config.CHAT_THREADS or hardware.get_cpu_cores()}")
    add(f"  采样      temp={config.CHAT_TEMP} top_p={config.CHAT_TOP_P} "
        f"top_k={config.CHAT_TOP_K} min_p={config.CHAT_MIN_P} "
        f"repeat_penalty={config.CHAT_REPEAT_PENALTY}")
    for n in plan.notes:
        add(f"  说明      {n}")
    add()

    # ---------- 组件 ----------
    add("【组件】")
    for label, p in (
        ("推理引擎", config.ENGINE),
        ("聊天模型", config.CHAT_MODEL),
        ("视觉投影", config.CHAT_MMPROJ),
        ("OCR 模型", config.OCR_MODEL),
        ("OCR 投影", config.OCR_MMPROJ),
        ("MiMo Code", config.MIMO_EXE),
    ):
        if p.is_file():
            add(f"  {label:10s} {p.name}  {p.stat().st_size / (1024**3):.2f} GB")
        else:
            add(f"  {label:10s} 【缺失】{p}")
    add()

    # 引擎版本
    ver = _run([str(config.ENGINE), "--version"]) if config.ENGINE.is_file() else ""
    add("【引擎版本】")
    add(f"  {ver.splitlines()[0] if ver else '（读不到）'}")
    mimover = _run([str(config.MIMO_EXE), "--version"]) if config.MIMO_EXE.is_file() else ""
    add(f"  MiMo Code: {mimover.splitlines()[0] if mimover else '（读不到）'}")
    add()

    # ---------- 模型指纹 ----------
    add("【模型指纹】")
    add(f"  聊天模型  {_sha256_quick(config.CHAT_MODEL)}")
    add(f"  OCR 模型  {_sha256_quick(config.OCR_MODEL)}")
    add()

    # ---------- 自检 ----------
    if checker is not None:
        add("【自检结果】")
        for status, text in checker.lines:
            if status:
                add(f"  {status} {text}")
            else:
                add(f"  {text}")
        add(f"  合计：通过 {checker.passed} · 注意 {checker.warned} · 缺失 {checker.failed}")
        add()
    else:
        add("【自检结果】")
        add("  （本次没跑自检，用 python tools/selfcheck.py --report 可以带上）")
        add()

    # ---------- 日志尾部 ----------
    add("【最近的错误（引擎日志尾部）】")
    for ln in _log_tail(config.LOGS / "engine-chat.log"):
        add(ln)
    add()
    add("【MiMo Code 日志尾部】")
    for ln in _log_tail(config.LOGS / "agent-server.log", 10):
        add(ln)
    add()

    # ---------- 配置 ----------
    add("【MiMo Code 配置】")
    cfg = config.MIMO_AGENT_DIR / "config" / "mimocode" / "mimocode.jsonc"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            add(f"  模型        {data.get('model')}")
            add(f"  权限        {len(data.get('permission', {}))} 项")
            add(f"  instructions {data.get('instructions')}")
        except Exception as e:
            add(f"  （解析失败：{e}）")
    else:
        add("  （还没生成）")
    add()

    add("=" * 72)
    add("  把整份内容复制出来即可定位问题。")
    add("=" * 72)
    return "\n".join(L)


def write_report(checker=None) -> Path:
    text = build_report(checker)
    out = config.LOGS / f"诊断报告-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    p = write_report()
    print(build_report())
    print(f"\n已保存到：{p}")
