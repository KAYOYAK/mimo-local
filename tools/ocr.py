"""
OCR 命令行工具 —— 给 AI 编程 agent 当工具用的。

为什么是这个形态：
  MiMo Code 有 bash 工具，模型能执行命令。与其去写 MCP server（还要装
  Python mcp 库），不如提供一个简单的命令行工具，让模型直接调。这是
  官方"plugin 注入工具"的简化版。

用法（给模型看的）：
    python ocr.py <图片路径> [--task doc|text|table|formula]

输出：识别的 Markdown 到 stdout，出错信息到 stderr。

引擎启动：如果 8090 没在跑，这个脚本会自己起一个，跑完留着（同一次会话里
后续调用就快了）。程序退出时由主界面负责关掉。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许从任意目录调用
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from core import client
from core import engine as eng


def ensure_ocr_engine() -> None:
    """确保 OCR 引擎在跑。

    ⚠ 这个函数起的引擎**不属于 UI 的 Engine 实例**，UI 退出时不会自动关它。
    所以这里把 pid 写进 logs/ocr.pid —— 主程序启动和退出时按 pid 收尸。
    不写 pid 的后果实测过：关掉程序后 1.3 GB 显存不释放，下次启动撞端口占用。
    """
    if eng.health_ok(config.OCR_PORT):
        return

    e = eng.make_ocr_engine()
    e.start()

    # 引擎对象 e 在这里就离开作用域了（Popen 句柄和日志句柄一起丢），
    # 所以必须立刻把 pid 落盘，否则这个进程再也没有人认领。
    pid = None
    try:
        pid = e.proc.pid if e.proc else None
    except Exception:
        pass
    if pid:
        eng.write_pid(config.OCR_PID_FILE, pid)


def main() -> int:
    ap = argparse.ArgumentParser(description="用本地 Xiaomi-OCR-0 识别图片")
    ap.add_argument("image", help="图片路径")
    ap.add_argument("--task", default="doc",
                    choices=["doc", "text", "table", "formula"],
                    help="识别任务类型（默认 doc=整页文档转 Markdown）")
    args = ap.parse_args()

    img = Path(args.image)
    if not img.is_absolute():
        img = Path.cwd() / img
    if not img.exists():
        print(f"找不到图片：{img}", file=sys.stderr)
        return 1

    try:
        ensure_ocr_engine()
        text = client.ocr_image(config.OCR_PORT, img, args.task)
        print(text)
        return 0
    except Exception as e:
        print(f"识别失败：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
