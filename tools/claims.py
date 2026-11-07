"""
声明断言：把 README 里写下的承诺变成会失败的检查。

思路来自 NULL 项目那边最值钱的一条方法论：文档里的「它不会做 X」
要么被代码保证，要么就该删掉。手工比对必然漂移，所以写成断言。

跑法：
    python tools/claims.py        正常输出
    python tools/claims.py -v     显示每条检查的细节

退出码：0 = 全部成立，1 = 有声明和实现不符
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

results: list[tuple[bool, str, str]] = []      # (通过, 声明, 证据)
skipped: list[str] = []


def check(ok: bool, claim: str, evidence: str):
    results.append((ok, claim, evidence))


def skip(claim: str, why: str):
    """打包成 exe 后有些源码读不到 —— 那是环境限制，不算声明被违反。"""
    skipped.append(f"{claim}（{why}）")


def _source_of(mod, name: str) -> str:
    """拿模块里某个函数的源码。打包后可能拿不到，返回空串。"""
    import inspect
    try:
        obj = getattr(mod, name, None)
        if obj is None:
            return ""
        return inspect.getsource(obj)
    except Exception:
        return ""


def _read_repo_file(rel: str) -> str:
    """读仓库里的源码文件。打包后没有这个文件，返回空串。"""
    p = config.ROOT / rel
    try:
        return p.read_text(encoding="utf-8") if p.is_file() else ""
    except Exception:
        return ""


# ---------- README 的每一句承诺 ----------

def claim_offline():
    """README：完全本地，不联网。"""
    from core.agent import CONFIG_TEMPLATE
    body = CONFIG_TEMPLATE % {"instructions": "[]", "ctx": 65536, "port": 8080}
    perm = json.loads(body)["permission"]
    web = perm.get("web")
    fetch = perm.get("webfetch")
    check(web == "deny" and fetch == "deny",
          "README 说「不联网」→ agent 的 web/webfetch 必须是 deny",
          f"web={web}, webfetch={fetch}")


def claim_auto_degrade():
    """README：显存不够会自动降级（压上下文 / 降 KV）。"""
    check(config.CHAT_CTX == 0,
          "README 说「显存低会自动降级」→ CHAT_CTX 必须是 0（0 = 自动推导）",
          f"CHAT_CTX={config.CHAT_CTX}")
    check(getattr(config, "CHAT_CTX_MAX", 0) >= 65536,
          "README 说「AI 编程需要 64K」→ CHAT_CTX_MAX 不能低于 65536",
          f"CHAT_CTX_MAX={getattr(config, 'CHAT_CTX_MAX', None)}")


def claim_64k_estimate():
    """README 的资源占用表建立在「64K」这个前提上。"""
    from core import hardware
    plan = hardware.plan_chat()
    check(plan.ctx in (8192, 16384, 32768, 65536),
          "上下文必须是按显存推导出的合法档位（不是拍脑袋的数）",
          f"ctx={plan.ctx}, kv={plan.kv_type}, auto_fit={plan.auto_fit}")


def claim_no_orphan():
    """README 的「已知限制」说关掉程序不会留残留。"""
    from core import engine
    has = all(callable(getattr(engine, n, None))
              for n in ("write_pid", "read_pid", "kill_pid"))
    check(has, "README 说「关掉程序不会留残留」→ engine 必须有 pid 收尸能力",
          "write_pid/read_pid/kill_pid 齐全" if has else "缺函数")

    # 打包后读不到源码，改用运行时检查
    src = _source_of(engine.Engine, "stop")
    if src:
        check("terminate" in src and "kill" in src,
              "Engine.stop() 必须真的终止进程（不是只关日志句柄）",
              "terminate + kill 都在")
    else:
        check(callable(getattr(engine.Engine, "stop", None)),
              "Engine.stop() 必须存在（关引擎不能只关日志句柄）",
              "stop 是方法")


def claim_ocr_as_tool():
    """README：OCR 是模型能调的工具，不是独立 Tab。"""
    from core import agent
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        md = agent._write_agents_md(Path(td))
        text = md.read_text(encoding="utf-8")
    check("ocr" in text.lower() and "<图片路径>" in text,
          "README 说「需要看图时模型自己调用本地 OCR」→ 必须生成工具说明",
          f"生成的说明 {len(text)} 字符")
    # 路径必须是当前安装位置，不能写死开发目录
    import sys as _sys
    if getattr(_sys, "frozen", False):
        check("--ocr" in text,
              "打包成 exe 后 tools/ocr.py 不在磁盘上 → 说明里必须用 --ocr 子命令",
              "用了 --ocr")
    else:
        check("tools" in text and "ocr.py" in text,
              "源码模式说明里必须指向真实存在的 ocr.py",
              "指向 tools/ocr.py")


def claim_no_source_rewrite():
    """README 说「切换模型不需要改源码」——实际是不能改。"""
    from tools import switch_model
    from core import settings as settings_mod
    has_settings = hasattr(settings_mod, "Settings")
    src = _source_of(switch_model, "set_model")
    if src:
        # 关键判据是「有没有去写文件」，不是「源码里提没提 config.py」
        # （docstring 里提到它是在解释为什么不能改，那不算违规）
        writes_files = "write_text" in src or ".write_bytes" in src
        check(not writes_files and has_settings,
              "换模型不能改写源码（打包后源码在压缩包里）→ 必须写 settings.json",
              f"写文件={writes_files}, Settings 可用={has_settings}")
    else:
        check(has_settings,
              "换模型必须通过 settings.json（Settings 类必须存在）",
              "core.settings.Settings 存在")


def claim_version_single_source():
    """README 的版本号只该有一处定义。"""
    check(bool(getattr(config, "VERSION", "")),
          "版本号必须有单一来源 config.VERSION",
          f"VERSION={getattr(config, 'VERSION', None)}")

    # 报告和 About 都该用它。打包后源码在 exe 里，用 inspect 拿；
    # 拿不到就跳过（环境限制，不是声明被违反）
    from tools import report as report_mod
    rsrc = _source_of(report_mod, "build_report") or _read_repo_file("tools/report.py")
    if rsrc:
        check("config.VERSION" in rsrc,
              "诊断报告必须带版本号（出问题时第一句话就是「你装的是哪版」）",
              "report.py 引用了 config.VERSION")
    else:
        skip("诊断报告必须带版本号", "打包后读不到源码")

    import ui_qt
    usrc = _read_repo_file("ui_qt.py")
    if usrc:
        check("config.VERSION" in usrc, "About 必须带版本号", "ui_qt.py 引用了")
    else:
        # 打包后 ui_qt 已经加载，直接查它的 show_about 有没有渲染 version
        import inspect
        try:
            src = inspect.getsource(ui_qt.MainWindow.show_about)
        except Exception:
            src = ""
        if src:
            check("config.VERSION" in src, "About 必须带版本号", "show_about 引用了")
        else:
            skip("About 必须带版本号", "打包后读不到源码")


def claim_abort():
    """README 的「已知限制」说一个回合可能跑很久 —— 那必须能中断。"""
    from core import agent
    has_abort = callable(getattr(agent, "abort_session", None))
    check(has_abort,
          "长回合必须能中断 → 必须有 abort 接口",
          "abort_session 存在" if has_abort else "缺函数")
    ui = _read_repo_file("ui_qt.py")
    if ui:
        t = ui
        check("btn_stop" in t and "stop_round" in t,
              "界面上必须有停止按钮", "btn_stop + stop_round")


def claim_audit_log():
    """bash 权限是 allow，那必须留痕迹。"""
    ui = _read_repo_file("ui_qt.py")
    if not ui:
        skip("bash 命令审计日志", "打包后读不到源码")
        return
    t = ui
    check("commands.log" in t,
          "bash: allow 意味着能跑任意命令 → 必须有命令审计日志",
          "commands.log 被写入")


def claim_no_hardcoded_dev_path():
    """AGENTS 说明里不能出现开发机的写死路径（除非就是开发机）。"""
    from core import agent
    import sys as _sys
    if getattr(_sys, "frozen", False):
        # 打包后 ROOT 是安装目录，生成出来的路径必须跟着走
        expected = str(config.ROOT / "tools" / "ocr.py")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            text = agent._write_agents_md(Path(td)).read_text(encoding="utf-8")
        # frozen 模式用 --ocr，不该出现 python 脚本路径
        ok = "--ocr" in text
        check(ok, "打包后 OCR 说明必须用 exe 自己的 --ocr（脚本不在磁盘上）",
              f"期望含 --ocr，实际 {'有' if ok else '没有'}")
        return

    # 源码模式：生成出来的路径必须等于 config.ROOT 推导出的那条，
    # 而不是写死的字面量 —— 这样把项目挪到别的盘也不会失效
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        text = agent._write_agents_md(Path(td)).read_text(encoding="utf-8")
    expected = str(config.ROOT / "tools" / "ocr.py")
    check(expected in text,
          "AGENTS 说明里的 ocr.py 路径必须按当前安装位置生成（不能写死）",
          f"期望包含 {expected}：{'是' if expected in text else '否'}")


# ---------- 入口 ----------

def main() -> int:
    verbose = "-v" in sys.argv

    print("=" * 64)
    print("  MiMo Local · 声明断言")
    print(f"  检查 README 的承诺是否被代码保证（v{config.VERSION}）")
    print("=" * 64)

    funcs = [v for k, v in sorted(globals().items()) if k.startswith("claim_")]
    for fn in funcs:
        try:
            fn()
        except Exception as e:
            results.append((False, f"{fn.__name__} 执行失败", str(e)))

    passed = sum(1 for ok, _, _ in results if ok)
    failed = [(c, e) for ok, c, e in results if not ok]

    print()
    for ok, claim, evidence in results:
        mark = "  [OK]  " if ok else "  [不符]"
        print(f"{mark} {claim}")
        if verbose or not ok:
            print(f"         证据：{evidence}")

    if skipped:
        print()
        for s in skipped:
            print(f"  [跳过] {s}")

    print()
    print("=" * 64)
    total = len(results) + len(skipped)
    if failed:
        print(f"  {passed}/{len(results)} 条成立，{len(failed)} 条不符"
              + (f"，{len(skipped)} 条跳过" if skipped else ""))
        print("  不符的要么改代码，要么改 README —— 两者不能同时成立。")
    else:
        print(f"  {passed}/{len(results)} 条全部成立"
              + (f"（{len(skipped)} 条因环境跳过）" if skipped else ""))
    print("=" * 64)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
