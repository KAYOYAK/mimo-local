"""
MiMo Local — 主窗口（PySide6）。

视觉严格对齐官方 Xiaomi MiMo Desktop 的暗色 token：
    bg #181818 / side #141414 / composer #1f1f1f / line #2e2e2e
    txt #f4f4f5 / txt2 #86868b / txt3 #636366
    accent #ff6a2b（橙）  danger #ff9b7f
    radius 8 / 12 / 16

流式：订阅 mimo serve 的 /event SSE，收到 message.part.updated 就重绘。
      重绘用节流（80ms），否则一个 token 一次重排会卡死界面。
"""
from __future__ import annotations

import html
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

import config
from core import agent as ag
from core import client
from core import engine as eng

# ================= 官方 token =================
C_BG       = "#181818"
C_SIDE     = "#141414"
C_COMPOSER = "#1f1f1f"
C_CARD     = "#282828"
C_LINE     = "#2e2e2e"
C_TXT      = "#f4f4f5"
C_TXT2     = "#86868b"
C_TXT3     = "#636366"
C_ACCENT   = "#ff6a2b"
C_ACCENT2  = "#ff8352"
C_OK       = "#4ade80"
C_WARN     = "#fbbf24"
C_ERR      = "#ff9b7f"

QSS = f"""
QWidget {{
    background: {C_BG};
    color: {C_TXT};
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px;
}}
QFrame#topbar {{ background: {C_SIDE}; }}
QFrame#sidebar {{ background: {C_SIDE}; }}
QFrame#divider {{ background: {C_LINE}; }}

QLabel#brand {{ font-size: 17px; font-weight: 600; color: {C_TXT}; }}
QLabel#brandLocal {{ font-size: 17px; font-weight: 600; color: {C_ACCENT}; }}
QLabel#status {{ color: {C_TXT3}; font-size: 12px; }}
QLabel#section {{ color: {C_TXT3}; font-size: 11px; }}
QLabel#value {{ color: {C_TXT}; font-size: 12px; }}
QLabel#path {{ color: {C_TXT2}; font-size: 11px; }}

QPushButton {{
    background: transparent;
    color: {C_TXT2};
    border: none;
    border-radius: 8px;
    padding: 7px 14px;
    font-size: 12px;
}}
QPushButton:hover {{ background: {C_CARD}; color: {C_TXT}; }}

QPushButton#primary {{
    background: {C_ACCENT};
    color: #12100e;
    font-weight: 600;
    border-radius: 8px;
    padding: 8px 20px;
}}
QPushButton#primary:hover {{ background: {C_ACCENT2}; }}
QPushButton#primary:disabled {{ background: #3a2a22; color: #7a6258; }}

QTextBrowser#chat {{
    background: {C_BG};
    border: none;
    padding: 8px 22px;
    selection-background-color: #33518a;
}}

QPlainTextEdit#input {{
    background: {C_COMPOSER};
    border: 1px solid {C_LINE};
    border-radius: 12px;
    padding: 10px 14px;
    color: {C_TXT};
    selection-background-color: #33518a;
    font-size: 13px;
}}
QPlainTextEdit#input:focus {{ border: 1px solid {C_ACCENT}; }}

QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #3a3a3a; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #4a4a4a; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


class Bridge(QObject):
    """线程 → 主线程的信号桥。事件流在后台线程跑，不能直接碰控件。"""
    event = Signal(object)
    status = Signal(str, str)      # text, kind
    fatal = Signal(str)


# ---------- 对话项 ----------

class Item:
    """对话区里的一块内容。part 更新时按 id 找回来改。"""

    __slots__ = ("id", "kind", "text", "tool", "status", "args", "role")

    def __init__(self, id_, kind, role="assistant"):
        self.id = id_
        self.kind = kind          # user | text | reasoning | tool | divider
        self.text = ""
        self.tool = ""
        self.status = ""
        self.args = ""
        self.role = role


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MiMo Local")
        self.resize(1180, 800)
        self.setStyleSheet(QSS)

        self.chat_engine: eng.Engine | None = None
        self.ocr_engine: eng.Engine | None = None
        self.agent: ag.AgentServer | None = None
        self.session_id: str | None = None
        self.workdir: Path | None = None
        self.busy = False
        self.ocr_live = False        # OCR 引擎是否已常驻（启动时决定）

        self.items: list[Item] = []
        self._by_id: dict[str, Item] = {}
        self._dirty = False
        self._echoes: dict[str, int] = {}   # 用户发过的原文 → 剩余可吞次数

        self.bridge = Bridge()
        self.bridge.event.connect(self._on_event)
        self.bridge.status.connect(self._set_status)
        self.bridge.fatal.connect(lambda m: QMessageBox.critical(self, "错误", m))

        # 启动时轮转一次日志。超限才动，没超限什么也不做。
        try:
            from core import logs as _logs
            _logs.rotate()
        except Exception:
            pass

        # 清理上次异常退出留下的 OCR 引擎（ocr.py 起的那个，没人认领）
        try:
            from core import engine as _eng
            if _eng.kill_pid(config.OCR_PID_FILE):
                pass    # 静默清理，不打扰用户
        except Exception:
            pass

        self._build()

        # 重绘节流：80ms 一次。不节流的话每个 token 都重排，界面直接卡死
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setInterval(80)
        self._repaint_timer.timeout.connect(self._flush)
        self._repaint_timer.start()

    # ================= 构建 =================

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---------- 顶栏 ----------
        top = QFrame(objectName="topbar")
        top.setFixedHeight(56)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(20, 0, 16, 0)
        tl.setSpacing(8)

        b1 = QLabel("MiMo", objectName="brand")
        b2 = QLabel("Local", objectName="brandLocal")
        tl.addWidget(b1)
        tl.addWidget(b2)
        tl.addSpacing(20)

        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {C_TXT3}; font-size: 12px;")
        tl.addWidget(self.dot)
        self.status = QLabel("未启动", objectName="status")
        tl.addWidget(self.status)
        tl.addStretch(1)

        self.lbl_dir = QLabel("未选目录", objectName="path")
        tl.addWidget(self.lbl_dir)
        tl.addSpacing(8)

        self.btn_dir = QPushButton("选择目录")
        self.btn_dir.clicked.connect(self.pick_dir)
        tl.addWidget(self.btn_dir)

        self.btn_engine = QPushButton("启动引擎", objectName="primary")
        self.btn_engine.clicked.connect(self.toggle_engine)
        tl.addWidget(self.btn_engine)

        # 工具菜单（自检 / 诊断报告 / 换模型 / 打开日志）
        self.btn_tools = QPushButton("⋯")
        self.btn_tools.setFixedWidth(36)
        self.btn_tools.setToolTip("工具")
        self._build_tools_menu()
        tl.addWidget(self.btn_tools)

        outer.addWidget(top)

        # ---------- 分隔线 ----------
        d = QFrame(objectName="divider")
        d.setFixedHeight(1)
        outer.addWidget(d)

        # ---------- 主体 ----------
        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # 侧栏
        side = QFrame(objectName="sidebar")
        side.setFixedWidth(210)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(18, 18, 14, 14)
        sl.setSpacing(4)

        sl.addWidget(QLabel("项目", objectName="section"))
        self.proj_label = QLabel("（未选择）", objectName="path")
        self.proj_label.setWordWrap(True)
        sl.addWidget(self.proj_label)
        sl.addSpacing(16)

        sl.addWidget(QLabel("模型", objectName="section"))
        sl.addWidget(QLabel("MiMo 9B", objectName="value"))
        sl.addWidget(QLabel("本地 · 64K · 视觉", objectName="path"))
        sl.addSpacing(10)
        sl.addWidget(QLabel("Xiaomi OCR", objectName="value"))
        sl.addWidget(QLabel("本地 · 工具调用", objectName="path"))
        sl.addStretch(1)

        self.ctx_label = QLabel("", objectName="path")
        self.ctx_label.setWordWrap(True)
        sl.addWidget(self.ctx_label)

        bl.addWidget(side)

        # 竖分隔
        vd = QFrame(objectName="divider")
        vd.setFixedWidth(1)
        bl.addWidget(vd)

        # 对话区
        self.chat = QTextBrowser(objectName="chat")
        self.chat.setOpenExternalLinks(True)
        bl.addWidget(self.chat, 1)

        outer.addWidget(body, 1)

        # ---------- 分隔线 ----------
        d2 = QFrame(objectName="divider")
        d2.setFixedHeight(1)
        outer.addWidget(d2)

        # ---------- 输入区 ----------
        comp = QWidget()
        cl = QHBoxLayout(comp)
        cl.setContentsMargins(20, 14, 20, 12)
        cl.setSpacing(10)

        self.input = QPlainTextEdit(objectName="input")
        self.input.setPlaceholderText("说要做什么…  Enter 发送 · Shift+Enter 换行")
        self.input.setFixedHeight(84)
        self.input.installEventFilter(self)
        cl.addWidget(self.input, 1)

        self.btn_send = QPushButton("发送", objectName="primary")
        self.btn_send.setFixedHeight(84)
        self.btn_send.setFixedWidth(88)
        self.btn_send.clicked.connect(self.send)
        cl.addWidget(self.btn_send)

        # 停止按钮：平时隐藏，忙的时候出现，盖在发送按钮右边
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setFixedHeight(84)
        self.btn_stop.setFixedWidth(72)
        self.btn_stop.setVisible(False)
        self.btn_stop.setStyleSheet(
            f"QPushButton {{ background: {C_CARD}; color: {C_ERR};"
            f" border: 1px solid {C_ERR}; border-radius: 8px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: #3a2320; }}"
            f"QPushButton:disabled {{ color: {C_TXT3}; border-color: {C_LINE}; }}"
        )
        self.btn_stop.clicked.connect(self.stop_round)
        cl.addWidget(self.btn_stop)

        outer.addWidget(comp)

        foot = QWidget()
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(22, 0, 22, 10)
        hint = QLabel("纯本地 · 不联网 · 不需要账号", objectName="path")
        fl.addWidget(hint)
        fl.addStretch(1)
        fl.addWidget(QLabel("模型加载约 15 秒", objectName="path"))
        outer.addWidget(foot)

        self._add(Item("welcome", "divider"))
        self._add_system_text(
            "纯本地 AI 编程助手。\n"
            "  1. 点「选择目录」，挑一个项目文件夹\n"
            "  2. 点「启动引擎」，等模型加载\n"
            "  3. 直接说要做什么 —— 它会读写文件、执行命令，需要看图时自己调本地 OCR\n"
        )

    # ---------- 输入框快捷键 ----------

    def eventFilter(self, obj, ev):
        if obj is self.input and ev.type() == ev.Type.KeyPress:
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                if ev.modifiers() & Qt.ShiftModifier:
                    return False          # Shift+Enter 换行
                self.send()
                return True
        return super().eventFilter(obj, ev)

    # ================= 工具菜单 =================

    def _build_tools_menu(self):
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.setStyleSheet(f"""
            QMenu {{ background: {C_COMPOSER}; color: {C_TXT};
                     border: 1px solid {C_LINE}; border-radius: 8px; padding: 6px; }}
            QMenu::item {{ padding: 8px 28px 8px 16px; border-radius: 6px; font-size: 12px; }}
            QMenu::item:selected {{ background: {C_CARD}; }}
            QMenu::separator {{ height: 1px; background: {C_LINE}; margin: 6px 8px; }}
        """)
        m.addAction("自检", self.run_selfcheck)
        m.addAction("声明断言", self.run_claims)
        m.addAction("生成诊断报告", self.make_report)
        m.addSeparator()
        m.addAction("切换模型…", self.switch_model)
        m.addAction("打开日志目录", self.open_logs)
        m.addSeparator()
        m.addAction("关于", self.show_about)
        self._tools_menu = m
        self.btn_tools.setMenu(m)

    def run_selfcheck(self):
        """跑自检，把结果贴进对话区。"""
        import io
        import contextlib

        self._add_system_text("正在自检…")
        self._dirty = True

        def work():
            buf = io.StringIO()
            try:
                import tools.selfcheck as sc
                # 自检会 print，捕获下来
                with contextlib.redirect_stdout(buf):
                    code = sc.main()
                out = buf.getvalue()
                # 去掉 ANSI 颜色码
                import re
                out = re.sub(r"\x1b\[[0-9;]*m", "", out)
                self._add_system_text(out.strip())
                self.bridge.status.emit("自检完成", "ok" if code == 0 else "err")
            except Exception as e:
                self._add_system_text(f"自检失败：{e}")
                self.bridge.status.emit("自检失败", "err")
            finally:
                self._dirty = True

        threading.Thread(target=work, daemon=True).start()

    def run_claims(self):
        """跑声明断言，把结果贴进对话区。

        为什么要有这个入口：一个只能靠命令行跑的工具，实际上没人跑。
        声明断言的价值全在「经常被跑」—— 不跑就退化成注释。
        """
        import io
        import contextlib
        import re

        self._add_system_text("正在检查 README 的承诺是否被代码兑现…")
        self._dirty = True

        def work():
            buf = io.StringIO()
            try:
                from tools import claims as claims_mod
                old = sys.argv
                sys.argv = ["claims"]
                try:
                    with contextlib.redirect_stdout(buf):
                        code = claims_mod.main()
                finally:
                    sys.argv = old
                text = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
                self._add_system_text(text.strip())
                self.bridge.status.emit(
                    "断言全部成立" if code == 0 else "断言有不符", "ok" if code == 0 else "err")
            except Exception as e:
                self._add_system_text(f"断言执行失败：{e}")
                self.bridge.status.emit("断言失败", "err")
            finally:
                self._dirty = True

        threading.Thread(target=work, daemon=True).start()

    def make_report(self):
        def work():
            try:
                from tools.report import write_report
                p = write_report()
                self._add_system_text(f"诊断报告已生成：\n{p}\n\n里面包含硬件、引擎配置、模型指纹、日志尾部，出问题时整份复制即可。")
                self.bridge.status.emit("报告已生成", "ok")
            except Exception as e:
                self._add_system_text(f"生成报告失败：{e}")
                self.bridge.status.emit("报告失败", "err")
            finally:
                self._dirty = True

        threading.Thread(target=work, daemon=True).start()

    def switch_model(self):
        try:
            from tools.switch_model import scan_models, find_mmproj, current_model
            import config as cfg
        except Exception as e:
            QMessageBox.warning(self, "换模型", f"模块加载失败：{e}")
            return

        models = scan_models()
        if not models:
            QMessageBox.information(
                self, "没有可选模型",
                f"{cfg.MODEL_DIR} 里没有 .gguf 模型文件。\n\n"
                f"把一个模型放进去，再回来切换。",
            )
            return

        cur = current_model()
        items = []
        for m in models:
            size = m.stat().st_size / (1024 ** 3)
            mm = find_mmproj(m)
            mark = "  ←当前" if m == cur else ""
            mm_note = "" if mm else "  （无投影，视觉不可用）"
            items.append(f"{m.name}   {size:.2f} GB{mm_note}{mark}")

        from PySide6.QtWidgets import QInputDialog
        choice, ok = QInputDialog.getItem(
            self, "切换模型", "选一个模型（重启程序后生效）：", items, 0, False,
        )
        if not ok:
            return
        idx = items.index(choice)
        chosen = models[idx]
        if chosen == cur:
            return

        try:
            from tools.switch_model import set_model
            set_model(chosen)
            self._add_system_text(
                f"已切换模型到 {chosen.name}\n重启程序生效（关掉再开）。"
            )
            self._dirty = True
        except Exception as e:
            QMessageBox.critical(self, "切换失败", str(e))

    def open_logs(self):
        try:
            import os
            os.startfile(str(config.LOGS))
        except Exception:
            pass

    def show_about(self):
        from core import hardware
        QMessageBox.information(
            self, "关于 MiMo Local",
            f"MiMo Local v{config.VERSION}\n"
            "纯本地 AI 编程助手\n\n"
            f"模型    MiMo V2.6 Distill Qwen 9B（llama.cpp）\n"
            f"引擎    {config.ENGINE.name}\n"
            f"OCR     Xiaomi-OCR-0（工具调用）\n"
            f"Agent   MiMo Code（mimo serve）\n\n"
            f"{hardware.summary()}\n\n"
            "不联网 · 不需要账号 · 所有数据在本机",
        )

    # ================= 渲染 =================

    def _add(self, item: Item) -> Item:
        self.items.append(item)
        if item.id:
            self._by_id[item.id] = item
        self._dirty = True
        return item

    def _add_system_text(self, text: str) -> None:
        it = Item("", "text")
        it.text = text
        it.role = "system"
        self.items.append(it)
        self._dirty = True

    def _flush(self):
        """把 items 渲染成 HTML。节流到 80ms 一次。"""
        if not self._dirty:
            return
        self._dirty = False

        parts = [f'<div style="line-height:1.65;">']
        for it in self.items:
            if it.kind == "divider":
                parts.append(f'<div style="color:{C_LINE}; margin:14px 0;">'
                             f'<hr style="border:none;border-top:1px solid {C_LINE};"></div>')
            elif it.role == "user":
                parts.append(
                    f'<div style="margin:16px 0 6px 0;">'
                    f'<span style="color:{C_ACCENT};font-weight:600;">你</span></div>'
                    f'<div style="color:{C_TXT};margin:0 0 4px 0;">'
                    f'{html.escape(it.text).replace(chr(10), "<br>")}</div>'
                )
            elif it.role == "system":
                parts.append(
                    f'<div style="color:{C_TXT3};font-size:12px;line-height:1.8;'
                    f'margin:10px 0;">'
                    f'{html.escape(it.text).replace(chr(10), "<br>")}</div>'
                )
            elif it.kind == "tool":
                icon = "✓" if it.status == "completed" else ("✗" if it.status == "error" else "·")
                color = C_OK if it.status == "completed" else (C_ERR if it.status == "error" else C_TXT3)
                args = f'<div style="color:{C_TXT3};font-size:11px;margin-left:22px;">{html.escape(it.args)}</div>' if it.args else ""
                parts.append(
                    f'<div style="margin:8px 0 2px 0;font-size:12px;">'
                    f'<span style="color:{color};">{icon}</span> '
                    f'<span style="color:{C_ACCENT};">{html.escape(it.tool)}</span></div>{args}'
                )
            elif it.kind == "reasoning":
                parts.append(
                    f'<div style="color:{C_TXT3};font-size:11px;margin:6px 0 6px 18px;'
                    f'line-height:1.5;">{html.escape(it.text).replace(chr(10), "<br>")}</div>'
                )
            else:  # text
                parts.append(
                    f'<div style="color:{C_TXT};margin:6px 0;line-height:1.7;">'
                    f'{html.escape(it.text).replace(chr(10), "<br>")}</div>'
                )
        parts.append("</div>")

        sb = self.chat.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 40
        self.chat.setHtml("".join(parts))
        if at_bottom:
            sb.setValue(sb.maximum())

    # ================= 状态 =================

    def _set_status(self, text: str, kind: str = "dim"):
        color = {"dim": C_TXT3, "busy": C_WARN, "ok": C_OK, "err": C_ERR}[kind]
        self.dot.setStyleSheet(f"color: {color}; font-size: 12px;")
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}; font-size: 12px;")

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.btn_send.setEnabled(not busy)
        self.btn_send.setText("思考中" if busy else "发送")
        # 忙的时候把「发送」变成「停止」——9B 一个任务常跑几十秒、调十几次工具，
        # 没有中断手段就是在逼用户杀进程（而杀进程会留下孤儿 OCR 引擎）
        if busy:
            self.btn_stop.setVisible(True)
            self.btn_stop.setEnabled(True)
        else:
            self.btn_stop.setVisible(False)

    def stop_round(self):
        """中断当前回合。"""
        if not self.busy or not self.session_id or not self.agent:
            return
        self.btn_stop.setEnabled(False)
        self.bridge.status.emit("正在中断…", "busy")

        def work():
            try:
                ok = ag.abort_session(self.agent, self.session_id)
                self._add_system_text("⏹ 已中断本轮。" if ok else "中断失败（可能这一轮已经结束了）。")
                self._dirty = True
            except Exception:
                pass
            # 不在这里设「就绪」——busy 要等 send() 的 finally 才清，
            # 状态栏说就绪而发送键还禁着，就是在撒谎。
            # 让状态停在「正在中断…」，由 send() 收尾。

        threading.Thread(target=work, daemon=True).start()

    # ================= 事件（后台线程 → 桥 → 这里）=================

    def _on_event(self, ev: ag.Event):
        if self.session_id and ev.session_id and ev.session_id != self.session_id:
            return

        if ev.type == "session.status":
            if ev.status == "busy":
                self.bridge.status.emit("思考中", "busy")
            elif ev.status == "idle":
                self.bridge.status.emit("就绪", "ok")
            return

        if ev.type == "message.part.updated" and ev.part:
            self._on_part(ev.part)
            return

        if ev.type == "permission.asked":
            # 配置里已经预授权了，理论上到不了这里。到了说明兜底失败 ——
            # 旧实现返回 0 就什么都不说，请求一直接卡到 900 秒超时，
            # 用户只看到永久「思考中」。现在必须说出来。
            try:
                n = self.agent.answer_pending_permissions() if self.agent else 0
                if n:
                    self.bridge.status.emit(f"自动放行 {n} 个权限", "busy")
                else:
                    raw = ev.raw or {}
                    perm = raw.get("permission", "?")
                    pats = raw.get("patterns", [])
                    self._add_system_text(
                        f"⚠ 卡在权限请求上：{perm}  {pats}\n"
                        f"   配置里本该已经预授权；这条说明兜底应答失败了。\n"
                        f"   这一轮会一直等到超时 —— 点「停止」中断，"
                        f"然后检查 agent 配置里的 permission 段。"
                    )
                    self._dirty = True
                    self.bridge.status.emit("卡在权限", "err")
            except Exception:
                pass

    def _on_part(self, part: dict):
        pid = part.get("id") or ""

        # 决策逻辑在 core/render.py 里（纯函数，自检能脱离 Qt 直接测）。
        # 这里只管把结果落到 items 上。
        from core import render as rnd
        step, reason = rnd.decide(part, self._echoes)
        if step is None:
            return

        # 同一个 part 反复更新（流式）→ 找到原来那块改内容
        it = self._by_id.get(pid) if pid else None
        if it is None:
            it = Item(pid, "text")
            self._add(it)

        if step.kind == "text":
            it.kind = "text"
            it.text = step.text
        elif step.kind == "reasoning":
            it.kind = "reasoning"
            it.text = step.text
        elif step.kind == "tool":
            it.kind = "tool"
            it.tool = step.tool
            it.status = step.status
            it.args = step.args
            # 审计：bash 命令全部落盘。配置是 bash: allow + external_directory: allow，
            # 意味着模型能在这台机器上跑任何命令 —— 出事之后这份日志是唯一能
            # 还原现场的东西。
            if step.tool in ("bash", "shell", "exec") and step.args:
                self._audit_command(step.args, step.status)

        self._dirty = True

    def _audit_command(self, args: str, status: str):
        """把模型执行的命令记进 logs/commands.log。

        为什么要有这份日志：配置里 bash 和 external_directory 都是 allow，
        模型能在这台机器上跑任何命令、读任何目录。出问题之后（文件被改坏、
        中文变乱码、路径被写错），这是唯一能还原「它到底执行了什么」的东西。
        """
        try:
            import json
            import time as _t
            detail = args
            # args 是 JSON 字符串，把 command 字段挑出来更好读
            try:
                obj = json.loads(args)
                detail = obj.get("command") or args
            except Exception:
                pass
            line = (f"{_t.strftime('%Y-%m-%d %H:%M:%S')}  [{status or '?'}]  "
                    f"{str(detail)[:2000]}\n")
            log = config.LOGS / "commands.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass    # 审计日志失败不能影响主流程

    def _reconcile(self, sid: str):
        """用消息链补齐 SSE 可能漏掉的 part。

        SSE 断流（进程抖动、网络、重连）时，界面会停在半截；而 POST
        /message 的返回值本身就是最终完整消息。所以每轮结束后拉一次消息链，
        把没渲染过的 part 补上。流式负责「快」，这里负责「准」。

        ⚠ 这个方法在 worker 线程被调用，**不能直接改 self.items** ——
        GUI 线程的 _flush 定时器正在读它们，会渲染到「写了一半的 Item」。
        所以走 bridge 把 part 送回 GUI 线程，由 _on_event 处理。
        """
        try:
            msgs = ag.get_messages(self.agent, sid)
        except Exception:
            return
        known = set(self._by_id.keys())
        for m in msgs:
            for p in m.get("parts", []):
                pid = p.get("id")
                if pid and pid not in known:
                    # 只有 GUI 线程能碰 items；这里只发信号
                    self.bridge.event.emit(ag.Event("message.part.updated", sid, part=p))

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择项目目录")
        if not d:
            return
        self.workdir = Path(d)
        self.proj_label.setText(str(self.workdir))
        self.lbl_dir.setText(self.workdir.name)
        self.session_id = None
        self.items.clear()
        self._by_id.clear()
        self._echoes.clear()      # 换项目后 echo 计数要清，否则会串
        self._add(Item("welcome2", "divider"))
        self._add_system_text(f"项目目录：{self.workdir}")

    def toggle_engine(self):
        if self.chat_engine and self.chat_engine.running:
            self._stop()
        else:
            self._start()

    def _start(self):
        if self.workdir is None:
            QMessageBox.information(self, "先选目录", "点「选择目录」挑一个项目文件夹。")
            return
        self.btn_engine.setEnabled(False)
        self._set_status("启动中", "busy")

        def work():
            try:
                self.bridge.status.emit("加载 9B 模型", "busy")
                self.chat_engine = eng.make_chat_engine()
                self.chat_engine.start(on_phase=lambda s: self.bridge.status.emit(s, "busy"))

                ctx = client.get_context_size(config.CHAT_PORT)

                # OCR 引擎也一起拉起来 —— 三个能力「同时在线」，不是按需切换。
                # 实测三个同占 11116 / 12227 MiB，余 1111 MiB。
                # 但显存吃紧时（比如用户开着别的程序）就别硬塞，退化成按需启动。
                from core import hardware as hw
                used = hw.get_nvidia_used_gb()
                total = hw.get_nvidia_vram_gb()
                spare = (total - used) * 1024 if total and used else 99999
                if spare > 2200:
                    self.bridge.status.emit("加载 OCR 模型", "busy")
                    try:
                        self.ocr_engine = eng.make_ocr_engine()
                        self.ocr_engine.start(on_phase=lambda s: self.bridge.status.emit(s, "busy"))
                        self.ocr_live = True
                    except Exception as e:
                        self.ocr_live = False
                        self._add_system_text(f"OCR 引擎没起来（{e}）—— 需要时会在调用时自动启动。")
                else:
                    self.ocr_live = False
                    self._add_system_text(
                        f"显存只剩 {spare / 1024:.1f} GB，没预加载 OCR 引擎。"
                        f"需要时会自动启动。"
                    )

                self.bridge.status.emit("启动 MiMo Code", "busy")
                self.agent = ag.AgentServer(config.AGENT_PORT)
                self.agent.on_event = lambda ev: self.bridge.event.emit(ev)
                # SSE 断流要说话 —— 否则界面会永久停在「思考中」
                self.agent.on_event_error = lambda msg: self.bridge.status.emit(msg, "err")
                self.agent.start(
                    on_phase=lambda s: self.bridge.status.emit(s, "busy"),
                    workdir=self.workdir,
                )

                self.bridge.status.emit("就绪", "ok")
                ocr_tag = "OCR 在线" if self.ocr_live else "OCR 按需"
                self.ctx_label.setText(f"上下文 {ctx // 1024}K\n9B + {ocr_tag}\n全本地推理")
                self.btn_engine.setText("停止引擎")
            except Exception as e:
                self.bridge.status.emit("启动失败", "err")
                self.btn_engine.setText("启动引擎")
                self.bridge.fatal.emit(str(e))
            finally:
                self.btn_engine.setEnabled(True)

        threading.Thread(target=work, daemon=True).start()

    def _stop(self):
        self.btn_engine.setEnabled(False)

        def work():
            try:
                for obj in (self.agent, self.chat_engine, self.ocr_engine):
                    if obj:
                        obj.stop()
                self.agent = self.chat_engine = self.ocr_engine = None
                self.ocr_live = False
                # 兜底：ocr.py 可能自己起过引擎（模型调 OCR 时），按 pid 收尸
                try:
                    from core import engine as _eng
                    _eng.kill_pid(config.OCR_PID_FILE)
                except Exception:
                    pass
                self.bridge.status.emit("已停止", "dim")
                self.btn_engine.setText("启动引擎")
                self.ctx_label.setText("")
                self._add_system_text("引擎已停止。")
            finally:
                self.btn_engine.setEnabled(True)

        threading.Thread(target=work, daemon=True).start()

    # ================= 发送 =================

    def send(self):
        if self.busy:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        if not (self.chat_engine and self.chat_engine.running and self.agent):
            QMessageBox.information(self, "引擎未启动", "先点「启动引擎」。")
            return
        if self.workdir is None:
            QMessageBox.information(self, "先选目录", "先选一个项目文件夹。")
            return

        self.input.clear()
        _k = text.strip()
        self._echoes[_k] = self._echoes.get(_k, 0) + 1
        u = Item("", "text", role="user")
        u.text = text
        self._add(u)
        self._set_busy(True)

        sid = self.session_id

        def work():
            try:
                nonlocal sid
                if not sid:
                    sid = ag.create_session(self.agent, self.workdir)
                    self.session_id = sid
                ag.send_message(self.agent, sid, text, timeout=900)
                # 对账：SSE 可能漏事件（断流/重连），用 POST 返回值对应的消息链补齐。
                # 流式用来「快」，这里用来「准」。
                self._reconcile(sid)
                self.bridge.status.emit("就绪", "ok")
            except Exception as e:
                self._add_system_text(f"⚠ {e}")
                self._dirty = True
                self.bridge.status.emit("出错", "err")
            finally:
                self._set_busy(False)
                self.bridge.status.emit("就绪", "ok")   # 和 busy 同时收尾，状态栏别撒谎

        threading.Thread(target=work, daemon=True).start()

    # ================= 退出 =================

    def closeEvent(self, ev):
        try:
            for obj in (self.agent, self.chat_engine, self.ocr_engine):
                if obj:
                    obj.stop()
            # 兜底：ocr.py 自己起的引擎不在上面三个里，按 pid 收尸
            from core import engine as _eng
            _eng.kill_pid(config.OCR_PID_FILE)
        except Exception:
            pass
        ev.accept()


def run():
    app = QApplication(sys.argv)
    app.setApplicationName("MiMo Local")
    w = MainWindow()
    w.show()
    return app.exec()
