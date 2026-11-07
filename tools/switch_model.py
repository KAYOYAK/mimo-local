"""
模型切换 —— 移植自 U 盘项目的 tools/换模型.ps1，但砍掉了「下载」。

为什么砍下载：U 盘版的下载逻辑（断点续传、镜像回退、SHA-256 校验、磁盘预检）
是为了让陌生电脑能离线用。自己用的软件，模型要么已经在本机，要么手动放进去，
没必要在程序里再写一套下载器。

保留的是「选择」：扫 model 目录，列出可用模型，切换默认模型并写回 config.py。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

# 模型扩展名
GGUF = ".gguf"


def scan_models(root: Path | None = None) -> list[Path]:
    """扫模型目录，返回可能的模型文件（排除投影文件）。"""
    root = root or config.MODEL_DIR
    if not root.is_dir():
        return []
    out = []
    for f in sorted(root.glob(f"*{GGUF}")):
        name = f.name.lower()
        # 投影文件不是模型
        if "mmproj" in name or ".mmproj." in name:
            continue
        out.append(f)
    return out


def find_mmproj(model: Path) -> Path | None:
    """给一个模型找配套的投影文件。

    按基名匹配，不按大小 —— U 盘项目踩过「选最大的那个」把别人的投影配错。
    """
    d = model.parent
    base = model.stem
    # 去掉量化后缀：MiMo-...-Q5_K_M → MiMo-...
    # 注意 Python 的 (?i) 只能写在正则开头（PowerShell 可以写中间），
    # 所以这里一律用 flags=re.I。
    base = re.sub(r"-(I?Q\d[A-Za-z0-9_]*|BF16|F16|F32)$", "", base, flags=re.I)

    candidates = []
    for f in d.glob(f"*{GGUF}"):
        n = f.name.lower()
        if "mmproj" not in n and ".mmproj." not in n:
            continue
        mm_base = f.stem
        mm_base = re.sub(r"^mmproj-", "", mm_base, flags=re.I)
        mm_base = re.sub(r"\.mmproj.*$", "", mm_base, flags=re.I)
        mm_base = re.sub(r"-(BF16|F16|F32|Q\d.*)$", "", mm_base, flags=re.I)
        if mm_base and mm_base.lower() in base.lower():
            candidates.append(f)
    return candidates[0] if candidates else None


def current_model() -> Path:
    return config.current_model()


def set_model(model: Path) -> None:
    """切换当前模型。

    **不改源码**（旧实现是正则替换 config.py，打包成 exe 后必然失败，
    而且源码模式中途被杀会留半截文件）。改的是 settings.json，
    config.current_model() 启动时读它。
    """
    from core.settings import Settings

    mm = find_mmproj(model)
    s = Settings(config.SETTINGS_FILE)
    s.set("chat_model", str(model))
    if mm:
        s.set("chat_mmproj", str(mm))
    else:
        # 没有配套投影：把旧的清掉，否则会拿上一个模型的投影去配这个模型
        s.delete("chat_mmproj")


def main() -> int:
    models = scan_models()
    if not models:
        print(f"模型目录里没有 .gguf 模型：{config.MODEL_DIR}")
        print("把一个 .gguf 放进去再跑这个脚本。")
        return 1

    cur = current_model()
    print("=" * 64)
    print("  选择模型")
    print("=" * 64)
    print(f"  模型目录：{config.MODEL_DIR}\n")
    for i, m in enumerate(models, 1):
        mark = " ←当前" if m == cur else ""
        size = m.stat().st_size / (1024 ** 3)
        mm = find_mmproj(m)
        mm_note = f"  +投影 {mm.name}" if mm else "  （无投影）"
        print(f"  [{i}] {m.name}")
        print(f"      {size:.2f} GB{mm_note}{mark}")
    print()

    try:
        pick = input(f"  选哪个？[1-{len(models)}，回车取消] ").strip()
    except (EOFError, KeyboardInterrupt):
        return 0
    if not pick:
        print("  取消。")
        return 0

    try:
        idx = int(pick) - 1
        if not (0 <= idx < len(models)):
            raise ValueError
    except ValueError:
        print("  输入不合法。")
        return 1

    chosen = models[idx]
    if chosen == cur:
        print("  已经是当前模型。")
        return 0

    set_model(chosen)
    print(f"\n  已切换到：{chosen.name}")
    print("  settings.json 已更新（当前模型已切换）。重启程序生效。")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
