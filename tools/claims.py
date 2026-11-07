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
    """README 的资源占用表建立在「按显存自动推导」这个前提上。

    表驱动测纯函数 derive_ctx —— 不是「返回值在某个集合里」那种假绿
    （24 GB 卡上返回 8192 也能过）。这里逐档验证边界。
    """
    from core import hardware as hw
    table = [
        (24.0, 65536, "24 GB 顶格 64K"),
        (12.0, 65536, "12 GB 给 64K"),
        (11.9, 65536, "实测机 11.9 GB 给 64K"),
        (10.0, 65536, "10 GB 边界仍是 64K"),
        (9.9, 32768, "9.9 GB 降到 32K"),
        (8.0, 32768, "8 GB 给 32K"),
        (7.9, 16384, "7.9 GB 降到 16K"),
        (6.0, 16384, "6 GB 给 16K"),
        (5.9, 8192, "5.9 GB 降到 8K"),
        (1.0, 8192, "1 GB 保底 8K"),
        (0.0, 8192, "读不到显存保底 8K"),
    ]
    bad = []
    for vram, want, desc in table:
        got = hw.derive_ctx(vram)
        if got != want:
            bad.append(f"{desc}: 期望 {want} 实际 {got}")
    check(not bad,
          "README 说「显存低会自动降级」→ derive_ctx 每档边界都要对",
          "全部档位正确" if not bad else "; ".join(bad))

    # KV 精度同理
    kv_bad = []
    for vram, want in ((12.0, "q8_0"), (9.0, "q8_0"), (8.9, "q4_0"), (0.0, "q4_0")):
        got = hw.derive_kv(vram)
        if got != want:
            kv_bad.append(f"{vram} GB: 期望 {want} 实际 {got}")
    check(not kv_bad,
          "README 说「显存紧张会压 KV 精度」→ derive_kv 边界要对",
          "全部正确" if not kv_bad else "; ".join(kv_bad))


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

    # 光有函数不够 —— 没人调用就等于注释（这正是原来 bug 的形态）
    ui_src = _read_repo_file("ui_qt.py")
    if ui_src:
        calls = ui_src.count("kill_pid(")
        check(calls >= 2,
              "pid 收尸必须在「启动」和「退出」两处被真正调用（不是只有函数）",
              f"ui_qt.py 里 kill_pid( 出现 {calls} 次")


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
    if not ui:
        skip("界面上必须有停止按钮", "打包后读不到源码")
        return
    # 光有按钮不够：按钮必须**接到**中断逻辑上，否则就是个装饰
    wired = "clicked.connect(self.stop_round)" in ui
    defined = "def stop_round" in ui
    check(wired and defined,
          "停止按钮必须真的接上 stop_round（不是摆着好看）",
          f"按钮={('btn_stop' in ui)}, connect={wired}, 方法={defined}")


def claim_audit_log():
    """bash 权限是 allow，那必须留痕迹。"""
    ui = _read_repo_file("ui_qt.py")
    if not ui:
        skip("bash 命令审计日志", "打包后读不到源码")
        return
    # 光有 "commands.log" 这个字符串不够（注释里提一句就过了）——
    # 必须同时有「定义」和「调用」
    defined = "def _audit_command" in ui
    called = "_audit_command(" in ui and ui.count("_audit_command(") >= 2
    check(defined and called,
          "bash: allow 意味着能跑任意命令 → 审计日志必须被定义且被调用",
          f"定义={defined}, 调用点数={ui.count('_audit_command(')}")


def claim_version_one_source():
    """版本号在全仓库只应出现一次定义。"""
    import re
    offenders = []
    skip_files = {"config.py"}
    for p in config.ROOT.rglob("*.py"):
        rel = p.relative_to(config.ROOT)
        if any(part in (".git", "build", "dist", "agent", "__pycache__") for part in rel.parts):
            continue
        if str(rel) in skip_files:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        # 只找「模块级的 VERSION = "x.y.z"」这种定义，不找引用
        for m in re.finditer(r'^VERSION\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"', text, re.M):
            offenders.append(f"{rel}: {m.group(0)}")
    check(not offenders,
          "版本号只能有一个来源（config.VERSION），其他地方不许再写死",
          "只有 config.py 定义" if not offenders else "; ".join(offenders))


def claim_readme_switches():
    """README 里提到的我们自己的 --开关 必须真的存在。"""
    readme = _read_repo_file("README.md")
    main_src = _read_repo_file("main.py")
    if not readme or not main_src:
        skip("README 的开关都存在", "打包后读不到源码")
        return
    import re
    flags = set(re.findall(r'--[a-z][a-z-]{2,20}', readme))
    # 只查我们自己的 CLI 开关（llama-server 那些不归 main.py 管）
    ours = {"--selfcheck", "--claims", "--report", "--ocr"}
    missing = sorted(f for f in flags if f in ours and f not in main_src)
    check(not missing,
          "README 提到的 --开关 必须都能在 main.py 里找到",
          "全部存在" if not missing else "缺: " + ", ".join(missing))


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
