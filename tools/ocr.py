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
    """确保 OCR 引擎在跑。"""
    if eng.health_ok(config.OCR_PORT):
        return
    e = eng.make_ocr_engine()
    e.start()
    # 起完就不管了 —— 进程独立，后续调用直接连端口


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
