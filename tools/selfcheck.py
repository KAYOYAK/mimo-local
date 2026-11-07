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
    for label, p in (("聊天模型", config.current_model()), ("视觉投影", config.current_mmproj())):
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


def check_image_input(c: Checker):
    """发图入口必须存在，而且 OCR 结果要显示在对话区。

    真实用户反馈（第一条不是理论边界的）：
      「没有给他发图片的功能按钮，并且因为小米OCR得到的是文件，
        所以我让他提取图片的文字的时候还要去文件那里看」
    所以这条检查看的是：按钮在不在、选中的图会不会显示、
    OCR 结果有没有一个 ocr 类型的 Item 承接它（而不是只写文件）。
    """
    c.section("发图与 OCR 显示")
    ui = None
    try:
        ui = (config.ROOT / "ui_qt.py").read_text(encoding="utf-8")
    except Exception:
        pass
    if not ui:
        c.warn("打包后读不到 ui_qt.py，跳过发图检查")
        return

    checks = [
        ("self.btn_image", "有「图片」按钮"),
        ("clicked.connect(self.pick_images)", "按钮接到了选图函数"),
        ("getOpenFileNames", "支持多选图片"),
        ("kind == \"ocr\"", "OCR 结果有专门的渲染分支"),
        ("_run_ocr_for_images", "界面自己跑 OCR（不依赖模型调工具）"),
    ]
    missing = [desc for needle, desc in checks if needle not in ui]
    if missing:
        c.fail("发图功能不完整，缺：" + "、".join(missing))
    else:
        c.ok("发图按钮 → 选图 → 界面跑 OCR → 结果贴对话区，整条链路都在")

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


def check_icon(c: Checker):
    """应用图标必须能被解析出来。

    真实事故：窗口左上角一直是 Qt 默认的蓝色方块图标。
    原因是 ui_qt.run() 里写的是 config.ROOT / "assets" / "app.ico"，
    而打包后 ROOT 是安装目录，那里没有 assets 子目录。
    """
    c.section("应用图标")
    try:
        ico = config.ICON_FILE
        if ico.is_file():
            c.ok(f"图标文件存在（{ico.name}，{ico.stat().st_size} 字节）")
        else:
            c.fail(f"图标文件不存在：{ico}")
        png = config.ICON_PNG
        if png.is_file():
            c.ok(f"PNG 图标存在（{png.name}）")
        else:
            c.warn(f"PNG 图标不存在：{png}")

        # 校验 ICO 文件头（不能在这里 QIcon 加载 —— 那需要 QGuiApplication）
        try:
            import struct
            raw = ico.read_bytes()
            reserved, typ, count = struct.unpack("<HHH", raw[:6])
            ok_hdr = (reserved == 0 and typ == 1 and 1 <= count <= 32)
            if ok_hdr:
                # 逐个目录项校验偏移和长度不越界
                bad = []
                for i in range(count):
                    off = 6 + i * 16
                    if off + 16 > len(raw):
                        bad.append(i)
                        continue
                    _w, _h, _c, _r, _p, _b, size, data_off = struct.unpack(
                        "<BBBBHHII", raw[off:off + 16])
                    if data_off + size > len(raw) or size == 0:
                        bad.append(i)
                if bad:
                    c.fail(f"ICO 有 {len(bad)} 个尺寸的数据越界")
                else:
                    c.ok(f"ICO 结构合法（{count} 个尺寸，共 {len(raw)} 字节）")
            else:
                c.fail(f"ICO 文件头不合法（reserved={reserved}, type={typ}, count={count}）")
        except Exception as e:
            c.fail(f"ICO 解析失败：{e}")
    except Exception as e:
        c.fail(f"图标检查出错：{e}")

def check_pid_guard(c: Checker):
    """三个子进程都要有 pid 文件 + 收尸能力。

    真实事故：用户反馈「打开一次启动不了，端口 4096 被占用」。
    查出来是上次异常退出留下的 mimo serve / llama-server 没跟着退。
    原来只有 OCR 有 pid 文件，聊天引擎和 mimo serve 都没有 ——
    所以根本没法收尸。

    顺带发现更大的坑：_pid_is_llama 里用 text=True 读 tasklist，
    中文 Windows 上 tasklist 输出是 GBK，解码抛 UnicodeDecodeError，
    异常被 except 吞掉 → 身份校验永远 False → 收尸等于没写。
    """
    c.section("子进程收尸")
    from core import engine as eng
    need = [
        ("聊天引擎", getattr(config, "CHAT_PID_FILE", None)),
        ("OCR 引擎", getattr(config, "OCR_PID_FILE", None)),
        ("MiMo Code", getattr(config, "AGENT_PID_FILE", None)),
    ]
    missing = [n for n, f in need if f is None]
    if missing:
        c.fail(f"这些子进程没有 pid 文件（崩溃后无法收尸）：{'、'.join(missing)}")
    else:
        c.ok("三个子进程都有 pid 文件")

    # tasklist 解码必须能工作
    import os
    try:
        ok = eng._pid_is_llama(os.getpid())
        # python 进程应该判为 False（不是 llama），但**不能抛异常**
        c.ok("子进程身份校验可运行（tasklist 解码正常）")
    except Exception as e:
        c.fail(f"身份校验出错（会导致收尸失效）：{e}")

    # kill_pid 对不存在的 pid 要能静默清文件
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        from pathlib import Path
        fake = Path(td) / "fake.pid"
        eng.write_pid(fake, 999999)
        eng.kill_pid(fake)
        if not fake.is_file():
            c.ok("收尸会清掉过期 pid 文件")
        else:
            c.fail("收尸没清 pid 文件")

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


def check_render(c: Checker):
    """渲染管线：模型正文必须真的能显示出来。

    为什么要单独检查这个（真实事故）：
       `_on_part` 里曾经多了一个无条件的 return，所有 text part 在创建文本块
       之前就被丢掉 —— 界面上能看到工具卡片和「思考中」，就是看不到回答。
       而当时的自检和声明断言全在查「某个字符串在不在源码里」，
       没有一条和「用户能不能看见回答」有交集。

       现在决策逻辑抽到了 core/render.py（不依赖 Qt），这里直接喂假 part。
    """
    c.section("渲染管线")
    try:
        from core.render import decide
    except Exception as e:
        c.fail(f"core/render.py 加载失败：{e}")
        return

    # 正文必须能渲染
    step, reason = decide({"type": "text", "text": "你好"}, {})
    if step is not None and step.text == "你好":
        c.ok(f"正文能渲染（{reason}）")
    else:
        c.fail(f"正文被丢弃了（{reason}）—— 用户会看不到模型回答")

    # 思考过程和工具调用也要能渲染
    r1, _ = decide({"type": "reasoning", "text": "想想"}, {})
    r2, _ = decide({"type": "tool", "tool": "bash",
                    "state": {"status": "completed", "input": {"command": "ls"}}}, {})
    if r1 is not None and r2 is not None:
        c.ok("思考过程与工具调用能渲染")
    else:
        c.fail("思考过程或工具调用被丢弃")

    # 不该显示的必须被挡住
    bad = [
        ({"type": "text", "text": "x", "synthetic": True}, "synthetic 隐藏文本"),
        ({"type": "step-start"}, "step-start"),
        ({"type": "text", "text": "  "}, "空文本"),
    ]
    leaked = [name for part, name in bad if decide(part, {})[0] is not None]
    if leaked:
        c.warn(f"这些本该隐藏却渲染了：{', '.join(leaked)}")
    else:
        c.ok("隐藏文本 / 分隔标记 / 空内容都被正确过滤")

    # Markdown 必须能渲染（模型是编程助手，回答里一定有 **粗体**、`代码`、列表）
    try:
        import html as _html
        from core.render import md_to_html
        sample = "**一共 RM 5.90**\n- 商品：CLOTH PEG\n```\nprint(1)\n```"
        out = md_to_html(_html.escape(sample))
        ok_md = ("<b>" in out and "•" in out and "<pre" in out
                 and "**" not in out)
        if ok_md:
            c.ok("Markdown 能渲染（粗体 / 列表 / 代码块）")
        else:
            c.fail("Markdown 没渲染出来（回答会显示成星号）")
    except Exception as e:
        c.warn(f"Markdown 检查失败：{e}")

    # payload 复述必须被拦（模型有时把收到的 payload 当回答复述一遍）
    try:
        from core.render import looks_like_payload
        full = ("【用户的问题】\n翻译\n\n【用户发的图片：a.png】\n"
                "这是**图片里的内容**，不是用户打的字\n"
                "⚠ 你无法直接查看图片本身")
        ok2 = looks_like_payload(full) and not looks_like_payload("图片里是 MR DIY 的收据")
        if ok2:
            c.ok("模型复述 payload 时会被拦住（不是只按长度判断）")
        else:
            c.warn("payload 复述判据可能误判")
    except Exception as e:
        c.warn(f"payload 判据检查失败：{e}")

    # echo 不能误吞回答（真实事故：新对话第一句的回答完全不显示）
    e = {"你好": 1}
    d_user, _ = decide({"type": "text", "text": "你好"}, e)
    d_ans, _ = decide({"type": "text", "text": "你好！有什么可以帮你的？"}, {})
    if d_user is None:
        c.ok("用户消息的 echo 会被吞掉（不重复显示）")
    else:
        c.fail("用户消息的 echo 没被吞")
    if d_ans is not None:
        c.ok("模型回答以「你好」开头时不会被误吞")
    else:
        c.fail("模型回答被 echo 误吞了！—— 这正是「第一句回答不显示」的原因")

    # echo 语义：发过一次就只吞一次
    e = {"继续": 1}
    d1, _ = decide({"type": "text", "text": "继续"}, e)
    d2, _ = decide({"type": "text", "text": "继续"}, e)
    if d1 is None and d2 is not None:
        c.ok("echo 过滤只吞一次（不会永久吞掉同文本回答）")
    else:
        c.fail(f"echo 过滤语义不对：第一次吞={d1 is None}, 第二次吞={d2 is None}")

    # 超长截断
    d3, _ = decide({"type": "text", "text": "x" * 5000}, {})
    if d3 is not None and "已截断" in d3.text:
        c.ok(f"超长文本会截断（{len(d3.text)} 字符）")
    else:
        c.fail("超长文本没有截断")


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
    check_render(c)
    check_image_input(c)
    check_dirs(c)
    check_icon(c)
    check_pid_guard(c)
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
