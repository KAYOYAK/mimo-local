"""
硬件检测与引擎配置推导。

移植自 U 盘项目的 launcher.ps1（那段是踩了一年坑才稳定的）：
  · 按显存决定上下文长度
  · 按显存紧张程度决定 KV 缓存精度（只压缓存，不动模型权重）
  · 显存装不下时改用 --fit on 让引擎自己决定放几层，
    而不是硬塞 99 层然后 OOM

一个关键教训（U 盘版原文注释）：
  「上面那段只按'显存多大'决定上下文，从没算过'模型本身多大' ——
    同一张 8 GB 卡，6.4 GB 的模型给 99 层正好，换 14 GB 的模型还是给 99 层，
    然后开跑就 OOM。更糟的是报错指向别处。」
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import config


# ---------- 基础探测 ----------

def _run(cmd: list[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        return r.stdout
    except Exception:
        return ""


def get_nvidia_vram_gb() -> float:
    """NVIDIA 显存（GB）。拿不到返回 0。

    注意：这里**不要**按显卡型号猜大小 —— 猜大了就是显存不足直接崩。
    """
    if not shutil.which("nvidia-smi"):
        # PATH 里没有就试固定路径
        for p in (r"C:\Windows\System32\nvidia-smi.exe",
                  r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
            if Path(p).exists():
                out = _run([p, "--query-gpu=memory.total", "--format=csv,noheader,nounits"])
                return _parse_mib(out)
        return 0.0
    out = _run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"])
    return _parse_mib(out)


def get_nvidia_used_gb() -> float:
    out = _run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"])
    return _parse_mib(out)


def _parse_mib(out: str) -> float:
    try:
        first = out.strip().splitlines()[0].strip()
        return round(float(first) / 1024, 1)
    except Exception:
        return 0.0


def get_ram_gb() -> float:
    """物理内存（GB）。"""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return round(m.ullTotalPhys / (1024 ** 3), 1)
    except Exception:
        return 0.0


def get_cpu_cores() -> int:
    """物理核心数。llama.cpp 的 --threads 用物理核心比逻辑线程更稳。"""
    try:
        import ctypes
        # GetLogicalProcessorInformation 复杂，先用 os.cpu_count 折半估算
        n = os.cpu_count() or 8
        # Ryzen 9 8945HX: 32 线程 / 16 核。用一半。
        return max(1, n // 2)
    except Exception:
        return 8


def model_size_gb(path: Path) -> float:
    try:
        return round(path.stat().st_size / (1024 ** 3), 1)
    except Exception:
        return 0.0


# ---------- 配置推导 ----------

@dataclass
class ChatPlan:
    ctx: int
    kv_type: str
    ngl: int
    auto_fit: bool
    notes: list[str] = field(default_factory=list)

    @property
    def kv_label(self) -> str:
        return {"f16": "f16", "q8_0": "q8_0", "q4_0": "q4_0"}.get(self.kv_type, self.kv_type)


def derive_ctx(vram_gb: float, ctx_max: int = 65536) -> int:
    """按显存推导上下文长度。

    抽成纯函数是为了能被声明断言直接测（表驱动），
    不用起引擎、不用 GPU。
    """
    if vram_gb >= 10:
        ctx = 65536
    elif vram_gb >= 8:
        ctx = 32768
    elif vram_gb >= 6:
        ctx = 16384
    elif vram_gb > 0:
        ctx = 8192
    else:
        ctx = 8192
    return min(ctx, ctx_max)


def derive_kv(vram_gb: float) -> str:
    """按显存推导 KV 缓存精度。只压缓存，不动模型权重。"""
    if vram_gb <= 0 or vram_gb < 9:
        return "q4_0"
    return "q8_0"


def plan_chat() -> ChatPlan:
    """按本机硬件推导聊天引擎参数。

    如果 config 里显式指定了 CHAT_CTX / CHAT_KV / CHAT_NGL，优先用配置值
    （显式配置压过自动推导，和 U 盘版命令行参数的优先级一致）。
    """
    notes: list[str] = []
    vram = get_nvidia_vram_gb()
    ram = get_ram_gb()
    model_gb = model_size_gb(config.current_model())

    # ---- 上下文长度 ----
    ctx_max = getattr(config, "CHAT_CTX_MAX", 65536)
    if config.CHAT_CTX > 0:
        # 显式配置（调试用）。仍然受上限约束，免得手滑写成 200K 直接 OOM。
        ctx = min(config.CHAT_CTX, ctx_max)
        notes.append(f"上下文 {ctx}（配置指定）")
    else:
        # 按显存推导。10 GB 这条线来自实测：
        # 11.9 GB 显存跑 64K + q8_0 + flash-attn 实测占 9880 MiB，余量够。
        if vram <= 0:
            notes.append("读不到显存大小，按保守值起步")
        ctx = derive_ctx(vram, ctx_max)
        if vram > 0:
            notes.append(f"上下文 {ctx}（按显存 {vram} GB 自动，上限 {ctx_max}）")

    # ---- KV 缓存精度 ----
    # 必须调 derive_kv，不要在这里再抄一份 if/elif：
    # 抄一份就有「门禁测的代码」和「产品跑的代码」两份，哪天只改一边，
    # claims 全绿而行为不变（或反过来）。现在两边是同一个函数。
    kv = config.CHAT_KV or derive_kv(vram)

    # ---- 内存不够时主动压 ----
    if ram and model_gb and ram < model_gb * 1.5:
        if ctx > 8192:
            ctx = 8192
        kv = "q4_0"
        notes.append(f"内存 {ram} GB 偏紧（模型 {model_gb} GB），已上下文压到 {ctx}、KV 降到 q4_0")

    # ---- 显存装不装得下 ----
    ngl = config.CHAT_NGL if config.CHAT_NGL is not None else 99
    auto_fit = False
    if vram > 0 and config.CHAT_NGL is None:
        kv_kb = {"f16": 32, "q8_0": 16, "q4_0": 8}[kv]
        kv_gb = ctx * kv_kb / (1024 * 1024)
        overhead = 1.2
        need = round(model_gb + kv_gb + overhead, 1)
        if need > vram:
            over = round(need - vram, 1)
            auto_fit = True
            notes.append(
                f"模型 {model_gb} + KV {round(kv_gb, 1)} + 缓冲 {overhead} = {need} GB，"
                f"超过显存 {vram} GB（差 {over} GB）→ 交给引擎自动决定层数"
            )

    return ChatPlan(ctx=ctx, kv_type=kv, ngl=ngl, auto_fit=auto_fit, notes=notes)


def summary() -> str:
    """一行硬件摘要，给自检和界面用。"""
    vram = get_nvidia_vram_gb()
    used = get_nvidia_used_gb()
    ram = get_ram_gb()
    cores = get_cpu_cores()
    parts = []
    parts.append(f"显存 {vram} GB" + (f"（已用 {used}）" if used else "") if vram else "显存 未知（非 NVIDIA 或驱动未装）")
    parts.append(f"内存 {ram} GB")
    parts.append(f"物理核 {cores}")
    return " · ".join(parts)
