"""
自检 —— 移植自 U 盘项目的 tools/自检.ps1（21 项）。

设计原则和 U 盘版一致：**只读，不改任何东西**。
目的是「一眼看出哪里坏了」，不是「自动修好」。

跑法：
    python tools/selfcheck.py          正常输出
    python tools/selfcheck.py --report 额外生成一份诊断报告
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from core import hardware

PASS, WARN, FAIL = "[OK]  ", "[注意]", "[缺失]"


class Checker:
    def __init__(self):
        self.passed = 0
        self.warned = 0
        self.failed = 0
        self.lines: list[tuple[str, str]] = []      # (status, text)

    def ok(self, text: str):
        self.passed += 1
        self.lines.append((PASS, text))
        print(f"  \033[32m{PASS}\033[0m {text}")

    def warn(self, text: str):
        self.warned += 1
        self.lines.append((WARN, text))
        print(f"  \033[33m{WARN}\033[0m {text}")

    def fail(self, text: str):
        self.failed += 1
        self.lines.append((FAIL, text))
        print(f"  \033[31m{FAIL}\033[0m {text}")

    def section(self, title: str):
        self.lines.append(("", f"--- {title} ---"))
        print(f"\n\033[36m{title}\033[0m")

    @property
    def total(self):
        return self.passed + self.warned + self.failed


def human(n: int) -> str:
    return f"{n / (1024 ** 3):.1f} GB"


def check_engine(c: Checker):
    c.section("推理引擎")
    exe = config.ENGINE
    if exe.is_file():
        c.ok(f"llama-server.exe  {human(exe.stat().st_size)}")
        # 关键运行库
        d = exe.parent
        need = ["ggml-base.dll", "ggml-cuda.dll", "ggml-cpu-x64.dll", "llama.dll"]
        missing = [n for n in need if not (d / n).exists()]
        if missing:
            c.fail(f"引擎目录缺少运行库：{', '.join(missing)}")
        else:
            c.ok(f"引擎运行库齐全（{len(need)} 个关键 DLL）")
        # 能不能跑
        try:
            r = subprocess.run([str(exe), "--version"], capture_output=True,
                               text=True, timeout=15,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            first = (r.stdout or r.stderr or "").strip().splitlines()
            if first:
                c.ok(f"引擎可执行：{first[0][:70]}")
            else:
                c.warn("引擎没输出版本信息")
        except Exception as e:
            c.fail(f"引擎无法执行：{e}")
    else:
        c.fail(f"引擎不存在：{exe}")


def check_models(c: Checker):
    c.section("模型")
    for label, p in (("聊天模型", config.CHAT_MODEL), ("视觉投影", config.CHAT_MMPROJ)):
        if p.is_file():
            c.ok(f"{label}  {p.name}  {human(p.stat().st_size)}")
        else:
            c.fail(f"{label} 缺失：{p}")

    # OCR
    if config.OCR_MODEL.is_file() and config.OCR_MMPROJ.is_file():
        c.ok(f"文档识别  {config.OCR_MODEL.name}  {human(config.OCR_MODEL.stat().st_size)}")
    elif config.OCR_MODEL.is_file():
        c.warn("OCR 模型在，但缺投影文件 —— 起得了引擎但看不见图")
    else:
        c.warn("OCR 模型不在 —— 模型就没法识别图片了")

    # MiMo Code
    if config.MIMO_EXE.is_file():
        c.ok(f"MiMo Code  {config.MIMO_EXE.name}  {human(config.MIMO_EXE.stat().st_size)}")
        try:
            r = subprocess.run([str(config.MIMO_EXE), "--version"], capture_output=True,
                               text=True, timeout=20,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            ver = (r.stdout or "").strip().splitlines()
            if ver:
                c.ok(f"MiMo Code 可执行：{ver[0][:60]}")
        except Exception as e:
            c.fail(f"MiMo Code 无法执行：{e}")
    else:
        c.fail(f"MiMo Code 缺失：{config.MIMO_EXE}")


def check_hardware(c: Checker):
    c.section("硬件")
    vram = hardware.get_nvidia_vram_gb()
    used = hardware.get_nvidia_used_gb()
    ram = hardware.get_ram_gb()
    cores = hardware.get_cpu_cores()
    plan = hardware.plan_chat()

    if vram > 0:
        c.ok(f"显存 {vram} GB（已用 {used} GB）")
        if vram >= 11:
            c.ok(f"显存足够跑 {plan.ctx // 1024}K 上下文")
        elif vram >= 8:
            c.warn(f"显存 {vram} GB 偏小，当前上下文 {plan.ctx // 1024}K")
        else:
            c.warn(f"显存 {vram} GB 很小，可能会很慢")
    else:
        c.warn("读不到 NVIDIA 显存（非 N 卡或驱动没装）")

    if ram > 0:
        c.ok(f"内存 {ram} GB")
    c.ok(f"物理核心 {cores} 个（引擎会用 {config.CHAT_THREADS or cores} 线程）")

    c.ok(f"引擎配置：上下文 {plan.ctx // 1024}K · KV {plan.kv_type} · "
         f"{'自动减层' if plan.auto_fit else f'{plan.ngl} 层卸载'}")


def check_dirs(c: Checker):
    c.section("目录与文件")
    for label, p, optional in (
        ("assets 目录", config.ASSETS, False),
        ("日志目录", config.LOGS, False),
        ("AGENTS.md（OCR 工具说明）", config.AGENTS_FILE, False),
        ("agent 数据目录", config.MIMO_AGENT_DIR, True),
    ):
        if p.exists():
            c.ok(f"{label}  {p}")
        elif optional:
            c.warn(f"{label} 不存在（第一次启动会自己建）")
        else:
            c.fail(f"{label} 不存在：{p}")

    # 日志目录可写
    try:
        probe = config.LOGS / f".write-{os.getpid()}"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        c.ok("日志目录可写")
    except Exception as e:
        c.fail(f"日志目录写不进去：{e}")


def check_ports(c: Checker):
    c.section("端口")
    from core.engine import port_in_use
    for port, name in ((config.CHAT_PORT, "聊天引擎"), (config.OCR_PORT, "OCR"),
                       (config.AGENT_PORT, "MiMo Code")):
        if port_in_use(port):
            c.warn(f"端口 {port} 已被占用（{name}）—— 如果程序没在跑，可能是上次残留")
        else:
            c.ok(f"端口 {port} 空闲（{name}）")


def check_python(c: Checker):
    c.section("运行环境")
    c.ok(f"Python {sys.version.split()[0]}")
    try:
        import PySide6
        c.ok(f"PySide6 {PySide6.__version__}")
    except ImportError:
        c.fail("PySide6 没装 —— 界面起不来（uv pip install PySide6-Essentials）")
    # 是否打包运行
    frozen = getattr(sys, "frozen", False)
    c.ok(f"运行方式：{'打包 exe' if frozen else 'Python 源码'}")
    c.ok(f"程序根目录：{config.ROOT}")

    # 磁盘空间
    try:
        import shutil as sh
        free = sh.disk_usage(config.ROOT).free
        if free > 5 * 1024 ** 3:
            c.ok(f"磁盘剩余 {free / 1024 ** 3:.1f} GB")
        else:
            c.warn(f"磁盘只剩 {free / 1024 ** 3:.1f} GB")
    except Exception:
        pass


def check_config(c: Checker):
    c.section("配置")
    p = config.MIMO_AGENT_DIR / "config" / "mimocode" / "mimocode.jsonc"
    if p.is_file():
        import json
        try:
            # jsonc 有注释，粗暴剥掉再解析
            text = p.read_text(encoding="utf-8")
            data = json.loads(text)
            perm = data.get("permission", {})
            model = data.get("model", "")
            if perm:
                c.ok(f"权限预授权 {len(perm)} 项（防止请求卡死）")
            else:
                c.warn("配置里没有 permission —— 模型访问目录外文件时会弹权限请求，可能卡住")
            if model:
                c.ok(f"默认模型 {model}")
            inst = data.get("instructions", [])
            if inst:
                c.ok(f"instructions {len(inst)} 个")
            else:
                c.warn("配置里没有 instructions —— 模型可能不知道 OCR 工具")
        except Exception as e:
            c.warn(f"配置解析失败：{e}")
    else:
        c.warn("配置文件还没生成（第一次启动引擎时才会写）")


def main() -> int:
    ap = argparse.ArgumentParser(description="MiMo Local 自检")
    ap.add_argument("--report", action="store_true", help="额外生成诊断报告")
    args = ap.parse_args()

    print("=" * 64)
    print("  MiMo Local · 自检")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 64)

    c = Checker()
    check_python(c)
    check_engine(c)
    check_models(c)
    check_hardware(c)
    check_dirs(c)
    check_ports(c)
    check_config(c)

    print("\n" + "=" * 64)
    color = "\033[32m" if c.failed == 0 else "\033[31m"
    print(f"  {color}通过 {c.passed} · 注意 {c.warned} · 缺失 {c.failed}\033[0m  （共 {c.total} 项）")
    print("=" * 64)

    if c.failed == 0 and c.warned == 0:
        print("  一切正常。")
    elif c.failed > 0:
        print("  有「缺失」项，程序可能起不来。看上面标红的行。")
    else:
        print("  能跑，但有「注意」项。不影响基本使用。")

    if args.report:
        from tools.report import write_report
        path = write_report(c)
        print(f"\n  诊断报告已写入：{path}")

    return 0 if c.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
