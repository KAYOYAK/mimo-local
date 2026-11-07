"""
日志轮转。

移植自 U 盘项目的做法，但更简单：启动时检查，超限就改名+裁剪。
不做后台线程 —— 自用软件，启动时清一次够了。

保留策略：
    engine-chat.log      最近 2 个（当前 + 1 个旧的）
    其他日志             最近 3 个
    上限                 单个 5 MB
"""
from __future__ import annotations

import shutil
from pathlib import Path

import config

MAX_MB = 5
KEEP = {"engine-chat.log": 2, "engine-ocr.log": 2, "agent-server.log": 3}
DEFAULT_KEEP = 2


def rotate(log_dir: Path | None = None) -> list[str]:
    """检查并轮转日志。返回做过的事（给自检/报告用）。"""
    d = log_dir or config.LOGS
    if not d.is_dir():
        return []

    actions = []
    max_bytes = MAX_MB * 1024 * 1024

    for f in d.iterdir():
        if not f.is_file() or not f.name.endswith(".log"):
            continue
        try:
            size = f.stat().st_size
        except Exception:
            continue
        if size < max_bytes:
            continue

        keep = KEEP.get(f.name, DEFAULT_KEEP)
        stem, suffix = f.stem, f.suffix

        # 把 .1 → .2，.2 → .3 …（从大到小移动，避免覆盖）
        for i in range(keep - 1, 0, -1):
            src = d / f"{stem}.{i}{suffix}"
            dst = d / f"{stem}.{i + 1}{suffix}"
            if src.exists():
                try:
                    if dst.exists():
                        dst.unlink()
                    src.rename(dst)
                except Exception:
                    pass

        try:
            target = d / f"{stem}.1{suffix}"
            if target.exists():
                target.unlink()
            f.rename(target)
            actions.append(f"{f.name} 超过 {MAX_MB} MB，已轮转为 {target.name}")
        except Exception:
            # 轮转失败（文件被占用）—— 那就原地裁剪，保住最新内容
            try:
                data = f.read_bytes()
                f.write_bytes(data[-max_bytes // 2:])
                actions.append(f"{f.name} 轮转失败，已裁剪保留最后一半")
            except Exception:
                pass

    # 清掉超出保留数的旧文件
    for f in list(d.glob("*.log.*")):
        try:
            parts = f.name.rsplit(".", 1)
            if len(parts) != 2:
                continue
            idx = int(parts[1])
            base = parts[0] + ".log"
            keep = KEEP.get(base, DEFAULT_KEEP)
            if idx >= keep:
                f.unlink()
                actions.append(f"删掉过旧日志 {f.name}")
        except Exception:
            pass

    return actions


def total_size(log_dir: Path | None = None) -> int:
    d = log_dir or config.LOGS
    if not d.is_dir():
        return 0
    return sum(f.stat().st_size for f in d.iterdir() if f.is_file())
