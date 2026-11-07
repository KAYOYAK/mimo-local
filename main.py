"""
MiMo Local — 入口。

纯本地 AI 编程助手：
  · MiMo Code 作为 agent 引擎（本地进程，非云端）
  · MiMo V2.6 Distill Qwen 9B 作为模型（llama.cpp，64K 上下文）
  · Xiaomi-OCR-0 作为工具（llama.cpp，模型自己调）

没有登录、没有账号、没有云端模型。

运行：pythonw main.py   （不弹黑窗口）
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config


def _fatal(msg: str, detail: str = "") -> None:
    text = msg + ("\n\n" + detail if detail else "")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "MiMo Local — 启动失败", 0x10)
    except Exception:
        print(text)


def _safe_print(text: str) -> None:
    """windowed 模式下 sys.stdout 是 None，print 会炸。"""
    try:
        if sys.stdout is not None:
            sys.stdout.write(text + "\n")
    except Exception:
        pass


def _call_without_our_args(fn):
    """调子工具时临时清空 sys.argv —— 它们自己的 argparse 不认我们的参数。"""
    old = sys.argv
    sys.argv = [old[0]] if old else ["mimo"]
    try:
        return fn()
    finally:
        sys.argv = old


def _run_cli(argv: list[str]) -> int | None:
    """处理命令行工具参数。

    windowed exe 没有控制台，所以结果一律写文件，再尽力打印一份
    （源码运行时能看到输出）。
    """
    if "--selfcheck" in argv:
        import io
        import contextlib
        buf = io.StringIO()
        try:
            from tools import selfcheck as sc
            with contextlib.redirect_stdout(buf):
                code = _call_without_our_args(sc.main)
        except Exception:
            buf.write(traceback.format_exc())
            code = 1
        text = buf.getvalue()
        out = config.LOGS / "自检结果.txt"
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
        except Exception:
            pass
        _safe_print(text)
        _safe_print(f"\n已写入：{out}")
        return code

    if "--report" in argv:
        try:
            from tools.report import build_report, write_report
            text = _call_without_our_args(build_report)
            p = config.LOGS / f"诊断报告-{__import__('datetime').datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            _safe_print(text)
            _safe_print(f"\n已保存到：{p}")
            return 0
        except Exception:
            _safe_print(traceback.format_exc())
            return 1

    return None


def main() -> int:
    # 0. 命令行工具参数
    cli = _run_cli(sys.argv[1:])
    if cli is not None:
        return cli

    # 1. 资源自查
    missing = config.check_paths()
    if missing:
        _fatal(
            "以下文件找不到：\n\n" + "\n".join("  · " + m for m in missing),
            f"配置文件：{config.ROOT / 'config.py'}",
        )
        return 1

    # 2. Qt 依赖
    try:
        from PySide6.QtWidgets import QApplication  # noqa: F401
    except ImportError:
        _fatal(
            "缺少 PySide6（界面库）。\n\n装法：\n    uv pip install PySide6-Essentials",
            traceback.format_exc(limit=2),
        )
        return 1

    # 3. 起界面
    try:
        import ui_qt
    except Exception:
        _fatal("界面模块加载失败。", traceback.format_exc(limit=4))
        return 1

    try:
        return ui_qt.run()
    except Exception:
        _fatal("运行出错。", traceback.format_exc(limit=6))
        return 1


if __name__ == "__main__":
    sys.exit(main())
