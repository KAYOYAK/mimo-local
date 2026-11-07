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
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QTextBrowser, QVBoxLayout, QWidget,
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
    sessions = Signal(object)      # 会话列表（后台线程拉到后送回主线程）
    loaded = Signal(object)        # 历史消息（切会话时）


# ---------- 对话项 ----------

class Item:
    """对话区里的一块内容。part 更新时按 id 找回来改。"""

    __slots__ = ("id", "kind", "text", "tool", "status", "args", "role", "_pending", "tool_uri", "tool_img_id")

    def __init__(self, id_, kind, role="assistant"):
        self.id = id_
        self.kind = kind          # user | text | reasoning | tool | divider
        self.text = ""
        self.tool = ""
        self.status = ""
        self.args = ""
        self.role = role
        self._pending = None
        self.tool_uri = ""
        self.tool_img_id = ""


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
        # 处理过的 part id（含被 echo 吞掉的）。
        # _reconcile 用它判断「补过没有」——只看 _by_id 会把吞掉的 part
        # 再渲染一遍，用户的问题就会重复出现。
        self._seen: set[str] = set()
        self._pending_images: list[Path] = []   # 待发送的图片
        self._img_uris: dict[str, bool] = {}     # 已注册进 QTextDocument 的图片
        self._expanded: set[str] = set()         # 用户手动展开的折叠块
        self._img_paths: dict[str, Path] = {}    # img_id → 原始路径（放大用）

        self.bridge = Bridge()
        self.bridge.event.connect(self._on_event)
        self.bridge.status.connect(self._set_status)
        self.bridge.fatal.connect(lambda m: QMessageBox.critical(self, "错误", m))
        self.bridge.sessions.connect(self._fill_sessions)
        self.bridge.loaded.connect(self._on_loaded)

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
        sl.addSpacing(14)

        # ---------- 对话列表 ----------
        conv_head = QWidget()
        chl = QHBoxLayout(conv_head)
        chl.setContentsMargins(0, 0, 0, 0)
        chl.setSpacing(4)
        chl.addWidget(QLabel("对话", objectName="section"))
        chl.addStretch(1)
        self.btn_new_chat = QPushButton("＋")
        self.btn_new_chat.setFixedSize(20, 20)
        self.btn_new_chat.setToolTip("新建对话")
        self.btn_new_chat.setCursor(Qt.PointingHandCursor)
        self.btn_new_chat.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C_ACCENT};"
            f" border: 1px solid {C_LINE}; border-radius: 4px; font-size: 13px;"
            f" padding: 0; }}"
            f"QPushButton:hover {{ border-color: {C_ACCENT}; }}"
        )
        self.btn_new_chat.clicked.connect(self.new_chat)
        chl.addWidget(self.btn_new_chat)
        sl.addWidget(conv_head)

        self.conv_list = QListWidget()
        self.conv_list.setObjectName("convlist")
        self.conv_list.setFixedHeight(190)
        self.conv_list.setStyleSheet(
            f"QListWidget#convlist {{ background: {C_BG}; border: 1px solid {C_LINE};"
            f" border-radius: 6px; outline: none; }}"
            f"QListWidget#convlist::item {{ color: {C_TXT2}; padding: 5px 7px;"
            f" border-bottom: 1px solid {C_LINE}; font-size: 11px; }}"
            f"QListWidget#convlist::item:selected {{ background: {C_CARD};"
            f" color: {C_TXT}; border-left: 2px solid {C_ACCENT}; }}"
            f"QListWidget#convlist::item:hover {{ background: {C_CARD}; }}"
        )
        self.conv_list.itemClicked.connect(self._on_conv_clicked)
        sl.addWidget(self.conv_list)
        sl.addSpacing(14)

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
        self.chat.setOpenLinks(False)          # 链接由我们自己处理（折叠/放大）
        self.chat.setOpenExternalLinks(False)
        self.chat.anchorClicked.connect(self._on_anchor)
        self.chat.setContextMenuPolicy(Qt.CustomContextMenu)
        self.chat.customContextMenuRequested.connect(self._chat_menu)
        self.chat.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.chat.setOpenExternalLinks(True)
        bl.addWidget(self.chat, 1)

        outer.addWidget(body, 1)

        # ---------- 分隔线 ----------
        d2 = QFrame(objectName="divider")
        d2.setFixedHeight(1)
        outer.addWidget(d2)

        # ---------- 输入区 ----------
        comp = QWidget()
        cv = QVBoxLayout(comp)
        cv.setContentsMargins(20, 10, 20, 12)
        cv.setSpacing(6)

        # 待发送图片条（没选图片时隐藏）
        self.image_bar = QWidget()
        self._image_bar_layout = QHBoxLayout(self.image_bar)
        self._image_bar_layout.setContentsMargins(0, 0, 0, 0)
        self._image_bar_layout.setSpacing(6)
        self._image_bar_layout.addStretch(1)
        self.image_bar.setVisible(False)
        cv.addWidget(self.image_bar)

        # 输入行
        row = QWidget()
        cl = QHBoxLayout(row)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)

        # 图片按钮：点它选图，选完的图会显示在上面的条里
        self.btn_image = QPushButton("图片")
        self.btn_image.setFixedHeight(84)
        self.btn_image.setFixedWidth(66)
        self.btn_image.setToolTip("选图片。发送时会先跑本地 OCR，识别结果直接贴在对话里")
        self.btn_image.clicked.connect(self.pick_images)
        cl.addWidget(self.btn_image)

        self.input = QPlainTextEdit(objectName="input")
        self.input.setPlaceholderText("说要做什么…  Enter 发送 · Shift+Enter 换行")
        self.input.setFixedHeight(84)
        self.input.installEventFilter(self)
        # 输入框的右键菜单也得中文化 —— Qt 自带的是英文
        self.input.setContextMenuPolicy(Qt.CustomContextMenu)
        self.input.customContextMenuRequested.connect(self._input_menu)
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

        cv.addWidget(row)
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
            "  3. 直接说要做什么 —— 它会读写文件、执行命令\n"
            "  4. 要看图就点左下角「图片」选一张 —— 发送时先跑本地 OCR，\n"
            "     识别结果直接贴在对话里（不用去翻文件），同时喂给模型\n"
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

    # ---------- 图片 ----------

    IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

    def pick_images(self):
        """选图片。可以多选。"""
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", "",
            "图片 (*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tif *.tiff)",
        )
        for f in files:
            p = Path(f)
            if p.is_file() and p.suffix.lower() in self.IMG_EXTS and p not in self._pending_images:
                self._pending_images.append(p)
        self._refresh_image_bar()

    def _refresh_image_bar(self):
        """重建待发送图片条：每张图显示缩略图 + 文件名，点 ✕ 去掉。"""
        lay = self._image_bar_layout
        while lay.count() > 1:          # 保留末尾的 stretch
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for i, p in enumerate(self._pending_images):
            # 一个小卡片：上缩略图，下文件名 + ✕
            chip = QFrame(objectName="imgchip")
            chip.setStyleSheet(
                f"QFrame#imgchip {{ background: {C_CARD}; border: 1px solid {C_LINE};"
                f" border-radius: 6px; }}"
            )
            cv = QVBoxLayout(chip)
            cv.setContentsMargins(6, 6, 6, 4)
            cv.setSpacing(3)

            thumb = QLabel()
            thumb.setFixedSize(72, 54)
            thumb.setAlignment(Qt.AlignCenter)
            from PySide6.QtGui import QPixmap
            pm = QPixmap(str(p))
            if not pm.isNull():
                thumb.setPixmap(pm.scaled(72, 54, Qt.KeepAspectRatio,
                                          Qt.SmoothTransformation))
            else:
                thumb.setText("?")
            thumb.setCursor(Qt.PointingHandCursor)
            thumb.setToolTip(f"点开看大图：{p.name}")
            thumb.mousePressEvent = lambda ev, path=p: self._zoom_path(path)
            cv.addWidget(thumb)

            name = p.name if len(p.name) <= 12 else p.name[:10] + "…"
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)
            lb = QLabel(name)
            lb.setStyleSheet(f"color: {C_TXT2}; font-size: 10px;")
            lb.setToolTip(str(p))
            row.addWidget(lb)
            x = QPushButton("✕")
            x.setFixedSize(14, 14)
            x.setCursor(Qt.PointingHandCursor)
            x.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {C_TXT3};"
                f" border: none; font-size: 10px; padding: 0; }}"
                f"QPushButton:hover {{ color: {C_ERR}; }}"
            )
            x.clicked.connect(lambda _=False, idx=i: self._remove_image(idx))
            row.addWidget(x)
            cv.addLayout(row)

            lay.insertWidget(i, chip)

        self.image_bar.setVisible(bool(self._pending_images))

    def _zoom_path(self, path: Path):
        """按路径放大图片（待发送区用，还没有 img_id）。"""
        try:
            import os
            os.startfile(str(path))
        except Exception:
            pass

    def _remove_image(self, idx: int):
        if 0 <= idx < len(self._pending_images):
            self._pending_images.pop(idx)
            self._refresh_image_bar()

    def _run_ocr_for_images(self, images: list[Path], task: str = "doc") -> list[tuple[Path, str, str]]:
        """对一批图片跑 OCR。返回 [(路径, 文字, 错误)]。

        为什么界面自己跑而不是等模型调工具：
          模型调 `--ocr` 时结果写进文件，还得再读一次 —— 用户看不到，
          而且 9B 经常把它念不全。界面直接跑，结果原样贴在对话里。
        """
        from core import client
        out = []
        for img in images:
            # 引擎没起就先起（显存够时启动阶段已经起过了）
            try:
                if not self.ocr_engine:
                    self.ocr_engine = eng.make_ocr_engine()
                if not self.ocr_engine.running:
                    self.bridge.status.emit("启动 OCR 引擎", "busy")
                    self.ocr_engine.start(on_phase=lambda s: self.bridge.status.emit(s, "busy"))
                text = client.ocr_image(config.OCR_PORT, img, task)
                out.append((img, text, ""))
            except Exception as e:
                out.append((img, "", str(e)))
        return out

    def _build_payload(self, text: str, done: list, failed: list) -> str:
        """把用户的问题 + 图片 OCR 结果拼成给模型的内容。

        为什么结构要这么讲究（实测 9B 的行为）：
          如果只是把「用户输入」和「OCR 文字」用换行连起来，模型会把它
          当成「一段需要转述的材料」，输出「以下是图片内容：…」。
          而用户真正要的是「把图里的信息当依据，回答我的问题」。

        所以结构是：问题在前 → 证据在中（明确标成"图片内容"）→
        要求在后（且明确说"不要照抄"）。
        """
        if not done and not failed:
            return text

        parts: list[str] = []

        # 1. 用户的问题
        if text:
            parts.append(f"【用户的问题】\n{text}")
        else:
            parts.append("【用户的情况】\n只发了图片，没有写文字说明。")

        # 2. 图片内容（作为证据，不是需要转述的材料）
        for img, txt in done:
            parts.append(
                f"【用户发的图片：{img.name}】\n"
                f"下面这张图片已经用本地 OCR 转成了文字（可能有少量识别误差，"
                f"不影响理解）。这是**图片里的内容**，不是用户打的字，"
                f"也是你能获得的**全部**图片信息：\n"
                f"<<<\n{txt}\n>>>\n"
                f"⚠ 你无法直接查看图片本身，也不需要去读任何文件或重新识别 —— "
                f"上面这段就是图片的全部内容。如果这里没有你要的信息，"
                f"就直接说「图片里没有这项信息」，不要去找文件。"
            )
        for img, err in failed:
            parts.append(f"【用户发的图片：{img.name}】\n这张图 OCR 识别失败（{err}）。")

        # 3. 明确要求 —— 放在最后，因为模型对最后一句最敏感
        if text:
            parts.append(
                "请**直接回答用户的问题**。上面的图片文字是事实依据："
                "需要引用时引用具体的条目/数字，但不要整段照抄，"
                "也不要只说「图片内容是…」。"
                "如果图片里的信息不足以回答，明确说缺什么。"
            )
        else:
            parts.append(
                "用户没写问题。请先看懂图片内容，再用中文说明："
                "这是什么、关键信息有哪些、有没有值得注意的地方。"
                "不要逐字复述，抓重点。"
            )

        return "\n\n".join(parts)

    def _on_anchor(self, url):
        """处理点击：折叠/展开、放大图片。"""
        s = url.toString()
        if s.startswith("fold:"):
            key = s[5:]
            if key in self._expanded:
                self._expanded.discard(key)
            else:
                self._expanded.add(key)
            self._dirty = True
            self._flush()
            return
        if s.startswith("zoom:"):
            self._zoom_image(s[5:])
            return

    def _zoom_image(self, img_id: str):
        """点缩略图看大图。优先用系统看图器，失败就在程序里弹窗。"""
        path = self._img_paths.get(img_id)
        if not path:
            return
        try:
            import os
            os.startfile(str(path))          # Windows 默认看图器
        except Exception:
            try:
                from PySide6.QtGui import QPixmap
                from PySide6.QtWidgets import QDialog, QLabel, QScrollArea
                dlg = QDialog(self)
                dlg.setWindowTitle(path.name)
                dlg.resize(1000, 780)
                lab = QLabel()
                pm = QPixmap(str(path))
                # 太大了就缩到窗口能放下，但比缩略图大得多
                if pm.width() > 960:
                    pm = pm.scaledToWidth(960, Qt.SmoothTransformation)
                lab.setPixmap(pm)
                sc = QScrollArea()
                sc.setWidget(lab)
                lay = QVBoxLayout(dlg)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.addWidget(sc)
                dlg.exec()
            except Exception:
                pass

    def _input_menu(self, pos):
        """输入框右键菜单（中文）。"""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        cur = self.input.textCursor()
        has_sel = cur.hasSelection()

        a = menu.addAction("撤销")
        a.setEnabled(self.input.document().isUndoAvailable())
        a.triggered.connect(self.input.undo)

        b = menu.addAction("重做")
        b.setEnabled(self.input.document().isRedoAvailable())
        b.triggered.connect(self.input.redo)

        menu.addSeparator()

        c = menu.addAction("剪切")
        c.setEnabled(has_sel)
        c.triggered.connect(self.input.cut)

        d = menu.addAction("复制")
        d.setEnabled(has_sel)
        d.triggered.connect(self.input.copy)

        e = menu.addAction("粘贴")
        e.setEnabled(bool(QApplication.clipboard().text()))
        e.triggered.connect(self.input.paste)

        f = menu.addAction("删除")
        f.setEnabled(has_sel)
        f.triggered.connect(lambda: self.input.textCursor().removeSelectedText())

        menu.addSeparator()

        g = menu.addAction("全选")
        g.triggered.connect(self.input.selectAll)

        h = menu.addAction("清空输入框")
        h.setEnabled(bool(self.input.toPlainText()))
        h.triggered.connect(self.input.clear)

        menu.exec(self.input.mapToGlobal(pos))

    def _copy_last_answer(self):
        """复制最后一条模型回答（只要正文，不含思考过程/工具调用）。"""
        for it in reversed(self.items):
            if it.kind == "text" and it.role != "user" and it.role != "system" and it.text.strip():
                QApplication.clipboard().setText(it.text)
                self.bridge.status.emit("已复制最后一条回答", "ok")
                return
        self.bridge.status.emit("没有可复制的回答", "err")

    def _copy_last_question(self):
        """复制最后一条我发的话。"""
        for it in reversed(self.items):
            if it.role == "user" and it.text.strip():
                QApplication.clipboard().setText(it.text)
                self.bridge.status.emit("已复制最后一条提问", "ok")
                return
        self.bridge.status.emit("没有可复制的提问", "err")

    def _copy_all_conversation(self):
        """复制整段对话（只含用户和模型的正文，不含思考/工具/系统消息）。"""
        lines = []
        for it in self.items:
            if it.role == "user":
                lines.append(f"【我】\n{it.text}")
            elif it.kind == "text" and it.role not in ("user", "system") and it.text.strip():
                lines.append(f"【AI】\n{it.text}")
        text = "\n\n".join(lines)
        if not text.strip():
            self.bridge.status.emit("对话是空的", "err")
            return
        QApplication.clipboard().setText(text)
        self.bridge.status.emit("已复制整段对话", "ok")

    def _chat_menu(self, pos):
        """聊天区右键菜单（中文）。"""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        cur = self.chat.textCursor()
        has_sel = cur.hasSelection()

        a = menu.addAction("复制选中")
        a.setEnabled(has_sel)
        a.triggered.connect(self.chat.copy)

        menu.addSeparator()

        # 用户说「全选把界面上全部文字都选了」—— 所以按内容给几个精确的选项
        b = menu.addAction("复制最后一条回答")
        b.triggered.connect(self._copy_last_answer)

        c = menu.addAction("复制最后一条提问")
        c.triggered.connect(self._copy_last_question)

        d = menu.addAction("复制整段对话")
        d.triggered.connect(self._copy_all_conversation)

        e = menu.addAction("全选（含思考与工具）")
        e.triggered.connect(self.chat.selectAll)

        menu.addSeparator()

        f = menu.addAction("展开全部")
        f.triggered.connect(self._expand_all)
        g = menu.addAction("折叠全部")
        g.triggered.connect(self._collapse_all)

        menu.addSeparator()
        h = menu.addAction("清空对话显示")
        h.triggered.connect(self._clear_view)
        i = menu.addAction("保存对话为文本…")
        i.triggered.connect(self._save_chat)

        menu.exec(self.chat.mapToGlobal(pos))

    def _expand_all(self):
        for it in self.items:
            if it.kind in ("reasoning", "tool", "ocr"):
                self._expanded.add(self._fold_key(it))
        self._dirty = True
        self._flush()

    def _collapse_all(self):
        self._expanded.clear()
        self._dirty = True
        self._flush()

    def _clear_view(self):
        self.items.clear()
        self._by_id.clear()
        self._expanded.clear()
        self._add(Item("welcome0", "divider"))
        self._dirty = True
        self._flush()

    def _save_chat(self):
        from PySide6.QtWidgets import QFileDialog
        f, _ = QFileDialog.getSaveFileName(
            self, "保存对话", str(Path.home() / "mimo-chat.txt"),
            "文本 (*.txt);;Markdown (*.md)")
        if not f:
            return
        try:
            Path(f).write_text(self.chat.toPlainText(), encoding="utf-8")
            self.bridge.status.emit("已保存", "ok")
        except Exception as e:
            self.bridge.status.emit(f"保存失败：{e}", "err")

    @staticmethod
    def _fold_key(it) -> str:
        """折叠状态用的唯一键。同一个 Item 在多次重绘之间要稳定。"""
        if it.id:
            return it.id
        # 没有 id 的（系统消息等）用内存地址 —— 同一次会话里稳定
        return f"{it.kind}-{id(it)}"

    # ================= 会话（历史对话）=================

    def refresh_sessions(self):
        """从 MiMo Code 拉会话列表，填到侧边栏。"""
        if not self.agent or not self.workdir:
            self.conv_list.clear()
            return
        agent, workdir = self.agent, self.workdir

        def work():
            try:
                ss = ag.list_sessions(agent, workdir)
            except Exception:
                ss = []
            self.bridge.sessions.emit(ss)

        threading.Thread(target=work, daemon=True).start()

    def _fill_sessions(self, sessions: list):
        """主线程：把会话列表画出来。"""
        self.conv_list.blockSignals(True)
        self.conv_list.clear()
        import datetime
        for s in sessions:
            title = (s.get("title") or "未命名").strip()
            if len(title) > 18:
                title = title[:16] + "…"
            ts = ((s.get("time") or {}).get("updated") or 0) / 1000
            try:
                when = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
            except Exception:
                when = ""
            item = QListWidgetItem(f"{title}\n{when}")
            item.setData(Qt.UserRole, s.get("id"))
            item.setToolTip(s.get("title") or "")
            self.conv_list.addItem(item)
            if s.get("id") == self.session_id:
                item.setSelected(True)
                self.conv_list.setCurrentItem(item)
        self.conv_list.blockSignals(False)

    def _on_conv_clicked(self, item):
        sid = item.data(Qt.UserRole)
        if not sid or sid == self.session_id or self.busy:
            return
        self.load_session(sid)

    def load_session(self, sid: str):
        """切换到某个会话，把历史消息渲染出来。"""
        self.session_id = sid
        self.items.clear()
        self._by_id.clear()
        self._seen.clear()
        self._expanded.clear()
        self._add(Item("welcome-load", "divider"))
        self._add_system_text("正在加载历史…")
        self._dirty = True
        self._flush()
        self.bridge.status.emit("加载历史", "busy")

        agent = self.agent

        def work():
            try:
                msgs = ag.get_messages(agent, sid)
            except Exception as e:
                self.bridge.loaded.emit({"error": str(e)})
                return
            self.bridge.loaded.emit({"messages": msgs})

        threading.Thread(target=work, daemon=True).start()

    def _on_loaded(self, payload: dict):
        """主线程：把历史消息铺进对话区。"""
        self.items.clear()
        self._by_id.clear()
        self._seen.clear()
        err = payload.get("error")
        if err:
            self._add_system_text(f"加载历史失败：{err}")
            self.bridge.status.emit("加载失败", "err")
            self._dirty = True
            self._flush()
            return

        msgs = payload.get("messages") or []
        n_user = n_ai = 0
        for m in msgs:
            info = m.get("info") or {}
            role = info.get("role")
            for p in (m.get("parts") or []):
                ptype = p.get("type")
                if ptype in ("step-start", "step-finish", "snapshot", "patch"):
                    continue
                if p.get("synthetic"):
                    continue
                if role == "user" and ptype == "text":
                    txt = (p.get("text") or "").strip()
                    if not txt:
                        continue
                    # 历史里的 user 消息是我们拼的 payload —— 只显示用户那一句，
                    # 不要把 OCR 全文和【用户的问题】结构再翻出来
                    if "【用户的问题】" in txt:
                        seg = txt.split("【用户的问题】", 1)[1]
                        for cut in ("【用户发的图片：", "请**直接回答", "⚠ 你无法直接查看"):
                            i = seg.find(cut)
                            if i > 0:
                                seg = seg[:i]
                        txt = seg.strip() or "（发了一张图片）"
                    it = Item("", "text", role="user")
                    it.text = txt
                    self._add(it)
                    n_user += 1
                elif ptype == "text":
                    txt = (p.get("text") or "").strip()
                    if not txt:
                        continue
                    it = Item(p.get("id") or "", "text")
                    it.text = txt
                    self._add(it)
                    n_ai += 1
                elif ptype in ("reasoning", "tool"):
                    self._on_part(p)
        if n_user == 0 and n_ai == 0:
            self._add_system_text("这个对话还没有内容。")
        self.bridge.status.emit("就绪", "ok")
        self._dirty = True
        self._flush()

    def new_chat(self):
        """新建对话。"""
        if self.busy:
            return
        self.session_id = None
        self.items.clear()
        self._by_id.clear()
        self._seen.clear()
        self._expanded.clear()
        self._add(Item("welcome-new", "divider"))
        self._add_system_text("新对话。直接说要做什么。")
        self.bridge.status.emit("就绪", "ok")
        self._dirty = True
        self._flush()
        self.refresh_sessions()

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

    def _ensure_image(self, path: Path) -> tuple[str, str]:
        """把图片注册进 QTextDocument，返回 (uri, img_id)。

        QTextBrowser 不认 file:// 的 <img>（安全限制 + setHtml 会重置文档），
        必须用 document().addResource() 显式注册。注册过的缓存起来，
        否则每次 80ms 重绘都重新解码一遍图片，界面会卡。

        img_id 用来做「点击放大」的链接标识（_img_paths 里存原路径）。
        """
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QImage, QTextDocument

        try:
            p = Path(path)
            if not p.is_file():
                return "", ""
            img_id = f"img{abs(hash(str(p)))}"
            uri = f"mimo-{img_id}"
            if uri in self._img_uris:
                return uri, img_id
            img = QImage(str(p))
            if img.isNull():
                return "", ""
            # 缩略图：宽度和高度都限制。只限宽度的话，竖版收据/长截图
            # 会缩成一条竖线占满整个窗口，把对话全挤出去。
            max_w, max_h = 420, 260
            if img.width() > max_w or img.height() > max_h:
                img = img.scaled(max_w, max_h, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
            self.chat.document().addResource(
                QTextDocument.ImageResource, QUrl(uri), img)
            self._img_uris[uri] = True
            self._img_paths[img_id] = p
            return uri, img_id
        except Exception:
            return "", ""

    def _flush(self):
        """把 items 渲染成 HTML。节流到 80ms 一次。"""
        if not self._dirty:
            return
        self._dirty = False

        # 先把这一轮要显示的图片注册好（setHtml 会把文档内容重置，
        # 但 document 对象还在，资源注册是挂在 document 上的）
        for it in self.items:
            if it.kind == "ocr" and it._pending:
                uri, img_id = self._ensure_image(it._pending)
                it.tool_uri = uri
                it.tool_img_id = img_id

        parts = [f'<div style="line-height:1.65;">']
        for it in self.items:
            if it.kind == "divider":
                parts.append(f'<div style="color:{C_LINE}; margin:14px 0;">'
                             f'<hr style="border:none;border-top:1px solid {C_LINE};"></div>')
            elif it.role == "user":
                # 我说的话在**右边**：用表格把内容推到右侧，左边留空。
                # 不是整体分成两栏，是每条消息各自对齐
                # （QTextBrowser 不支持 flex/float，只能靠表格实现）。
                # 留 42% 空，最多占 58% —— 太宽就不像气泡了。
                body = html.escape(it.text).replace(chr(10), "<br>")
                parts.append(
                    f'<table width="100%" cellpadding="0" cellspacing="0" border="0"'
                    f' style="margin:16px 0 8px 0;">'
                    f'<tr><td width="42%"></td>'
                    f'<td width="58%" bgcolor="#2b2b2b">'
                    f'<table width="100%" cellpadding="10" cellspacing="0" border="0"><tr>'
                    f'<td style="color:{C_TXT};line-height:1.65;">{body}</td>'
                    f'</tr></table></td></tr></table>'
                )
            elif it.role == "system":
                parts.append(
                    f'<div style="color:{C_TXT3};font-size:12px;line-height:1.8;'
                    f'margin:10px 0;">'
                    f'{html.escape(it.text).replace(chr(10), "<br>")}</div>'
                )
            elif it.kind == "tool":
                # 工具调用：默认折叠成一行，点开看完整参数。
                # 一个任务常调十几次工具，全展开会把回答埋掉。
                key = self._fold_key(it)
                open_ = key in self._expanded
                icon = "✓" if it.status == "completed" else ("✗" if it.status == "error" else "·")
                color = C_OK if it.status == "completed" else (C_ERR if it.status == "error" else C_TXT3)
                arrow = "▼" if open_ else "▶"
                head = (f'<a href="fold:{key}" style="text-decoration:none;color:{C_TXT3};">'
                        f'<span style="color:{color};">{arrow} {icon} {html.escape(it.tool)}</span>'
                        f'</a>')
                if it.args:
                    head += f' <span style="color:{C_TXT3};font-size:11px;">' \
                            f'{html.escape(it.args[:90])}</span>'
                if open_ and it.args:
                    parts.append(
                        f'<div style="margin:8px 0 2px 0;font-size:12px;">{head}</div>'
                        f'<div style="color:{C_TXT3};font-size:11px;margin:2px 0 6px 22px;'
                        f'font-family:Consolas,monospace;line-height:1.5;">'
                        f'{html.escape(it.args)}</div>'
                    )
                else:
                    parts.append(
                        f'<div style="margin:6px 0;font-size:12px;">{head}</div>'
                    )
            elif it.kind == "ocr":
                # 用户发的图片 —— 和用户消息一样在**右边**。
                # 缩略图默认显示（确认发的是哪张），OCR 全文默认折叠
                # —— 用户要看的是回答，不是识别原文刷屏。
                key = self._fold_key(it)
                open_ = key in self._expanded
                arrow = "▼" if open_ else "▶"
                nchars = len(it.text or "")
                img_html = ""
                if it.tool_uri:
                    tag = (f'<a href="zoom:{it.tool_img_id}" style="text-decoration:none;">'
                           f'<img src="{it.tool_uri}" style="border-radius:6px;"></a>')
                    img_html = f'<div style="margin:0 0 8px 0;">{tag}</div>'
                link = (f'<a href="fold:{key}" style="text-decoration:none;color:{C_ACCENT};'
                        f'font-size:11px;">{arrow} 📎 {html.escape(it.tool)}'
                        f' <span style="color:{C_TXT3};">（OCR {nchars} 字，点开看全文）</span></a>')
                body_html = ""
                if open_:
                    body_html = (
                        f'<div style="margin-top:8px;color:{C_TXT};'
                        f'font-family:Consolas,monospace;font-size:12px;line-height:1.6;">'
                        f'{html.escape(it.text).replace(chr(10), "<br>")}</div>')
                # 右对齐：左 22% 空白 + 右 78% 内容（和用户消息同一侧）
                parts.append(
                    f'<table width="100%" cellpadding="0" cellspacing="0" border="0"'
                    f' style="margin:8px 0 10px 0;">'
                    f'<tr><td width="22%"></td>'
                    f'<td width="78%" bgcolor="{C_CARD}">'
                    f'<table width="100%" cellpadding="10" cellspacing="0" border="0"><tr>'
                    f'<td>{img_html}{link}{body_html}</td></tr></table>'
                    f'</td></tr></table>'
                )
            elif it.kind == "reasoning":
                # 思考过程：默认折叠。9B 会用英文自言自语很长一段，
                # 展开会把真正的回答挤到看不见的地方。
                key = self._fold_key(it)
                open_ = key in self._expanded
                arrow = "▼" if open_ else "▶"
                nchars = len(it.text or "")
                if open_:
                    parts.append(
                        f'<div style="margin:6px 0 2px 18px;font-size:11px;">'
                        f'<a href="fold:{key}" style="text-decoration:none;color:{C_TXT3};">'
                        f'{arrow} 思考过程</a></div>'
                        f'<div style="color:{C_TXT3};font-size:11px;margin:2px 0 6px 32px;'
                        f'line-height:1.55;">'
                        f'{html.escape(it.text).replace(chr(10), "<br>")}</div>'
                    )
                else:
                    parts.append(
                        f'<div style="margin:4px 0 4px 18px;font-size:11px;">'
                        f'<a href="fold:{key}" style="text-decoration:none;color:{C_TXT3};">'
                        f'{arrow} 思考过程 <span style="color:{C_TXT3};opacity:0.7;">'
                        f'（{nchars} 字）</span></a></div>'
                    )
            else:  # text —— AI 的回答在**左边**
                body = html.escape(it.text).replace(chr(10), "<br>")
                parts.append(
                    f'<div align="left" style="color:{C_ACCENT};font-size:11px;'
                    f'margin:10px 0 2px 2px;">MiMo</div>'
                    f'<div style="color:{C_TXT};margin:0 2px 8px 2px;'
                    f'line-height:1.7;">{body}</div>'
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
            # 被吞掉的 part 也要记成「见过」。
            # 否则 _reconcile 会发现它不在 _by_id 里、重新 emit 一遍，
            # 而那时 _echoes 已经减到 0 → 用户自己问的话会被当成模型
            # 回答在下面再出现一次。
            if pid:
                self._seen.add(pid)
            return

        # 没有 id 的 part 无法去重：流式更新时每次都会新建一个块，
        # 界面会出现重复内容。宁可不显示。
        # ⚠ 但**审计不能跟着被削弱** —— `bash: allow` 意味着模型能跑任何
        # 命令，commands.log 是出事之后唯一能还原现场的东西。所以审计在
        # 这个 return 之前做掉（审计和渲染是两件事）。
        if not pid:
            self._maybe_audit(step)
            return

        # 同一个 part 反复更新（流式）→ 找到原来那块改内容
        it = self._by_id.get(pid)
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
            self._maybe_audit(step)

        self._dirty = True

    def _maybe_audit(self, step):
        """工具调用要不要记审计日志。

        抽出来的原因：渲染有两条路径（有 id / 没 id），审计必须两条都覆盖。
        之前审计写在「有 id」分支里，没 id 的 part 直接 return 了 ——
        虽然是理论边界（MiMo Code 的 part 一般都有 id），但 commands.log
        是 bash: allow 下唯一能还原现场的东西，不该被渲染去重顺带削弱。
        """
        if step is None:
            return
        if step.kind == "tool" and step.tool in ("bash", "shell", "exec") and step.args:
            self._audit_command(step.args, step.status)

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
        known = set(self._by_id.keys()) | self._seen
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
        self._echoes.clear()
        self._seen.clear()      # 换项目后 echo 计数要清，否则会串
        self._add(Item("welcome2", "divider"))
        self._add_system_text(f"项目目录：{self.workdir}")
        self.refresh_sessions()

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
                # 把已有的历史对话列出来
                self.refresh_sessions()
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
        images = list(self._pending_images)

        # 只发图片、不写字也算一次发送
        if not text and not images:
            return
        if not (self.chat_engine and self.chat_engine.running and self.agent):
            QMessageBox.information(self, "引擎未启动", "先点「启动引擎」。")
            return
        if self.workdir is None:
            QMessageBox.information(self, "先选目录", "先选一个项目文件夹。")
            return

        self.input.clear()
        self._pending_images.clear()
        self._refresh_image_bar()

        # 用户消息
        shown = text or ("（看图）" if images else "")
        _k = shown.strip()
        self._echoes[_k] = self._echoes.get(_k, 0) + 1
        u = Item("", "text", role="user")
        u.text = shown
        self._add(u)

        # 图片先占位（OCR 结果稍后填）
        for img in images:
            st = Item("", "ocr")
            st.tool = img.name
            st.text = "识别中…"
            self._add(st)
            st._pending = img

        self._set_busy(True)

        sid = self.session_id

        def work():
            try:
                nonlocal sid, text

                # ---------- 1. 有图就先跑 OCR，结果直接贴对话区 ----------
                ocr_done: list[tuple[Path, str]] = []      # 成功的
                ocr_failed: list[tuple[Path, str]] = []    # 失败的
                if images:
                    self.bridge.status.emit("识别图片", "busy")
                    results = self._run_ocr_for_images(images)
                    for img, txt, err in results:
                        # 找到占位 Item 填内容
                        for it in self.items:
                            if it.kind == "ocr" and it.tool == img.name and it.text == "识别中…":
                                it.text = txt if txt else f"识别失败：{err}"
                                break
                        if txt:
                            ocr_done.append((img, txt))
                        else:
                            ocr_failed.append((img, err))
                    self._dirty = True

                # ---------- 2. 拼成模型能「当信息用」的内容 ----------
                # 关键：不能让模型以为那是用户打的字，也不能暗示它「转述一下」。
                # 实测 9B 会照着最后一个指令做 —— 所以结构必须是
                # 「问题在前、证据在中、明确要求在后」。
                payload = self._build_payload(text, ocr_done, ocr_failed)

                # 关键：payload 也要记进 echo。
                # 模型会把收到的 user message 回显到事件流里，而界面显示的
                # 只是「（看图）」—— 不记 payload 的话，它会以正文的样式
                # 再渲染一遍，用户会看到 OCR 结果重复两次。
                _pk = payload.strip()
                if _pk:
                    self._echoes[_pk] = self._echoes.get(_pk, 0) + 1

                if not sid:
                    sid = ag.create_session(self.agent, self.workdir)
                    self.session_id = sid
                ag.send_message(self.agent, sid, payload, timeout=900)
                # 对账：SSE 可能漏事件（断流/重连），用 POST 返回值对应的消息链补齐。
                # 流式用来「快」，这里用来「准」。
                self._reconcile(sid)
                self.refresh_sessions()   # 标题/更新时间会变
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
