"""Web 前端面板：用 QtWebView 内嵌 chat-demo（DeepSeek 官方开源的 MIT 前端）。

设计上不动 ui_qt.MainWindow 原有的构建逻辑，只是把它的 centralWidget
包进一个 QStackedWidget，再在旁边放这一页。

为什么用 QtWebView 而不是 QtWebEngine：
  PySide6 6.12 起 QtWebEngineWidgets 不再随 wheel 提供，QtWebView 是
  官方替代——Windows 上走系统 WebView2（本机已装 157.0.4341），
  体积和内存都比 WebEngine 小一个量级。

chat-demo 是纯静态站点，需要用 HTTP 托管（file:// 下 IndexedDB 不可用）。
"""
from __future__ import annotations

import http.server
import socketserver
import threading
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

try:
    from PySide6.QtWebView import QWebView
    _WEBVIEW_OK = True
except Exception:                                    # noqa: BLE001
    QWebView = None                                  # type: ignore[assignment]
    _WEBVIEW_OK = False


def resource_dir() -> Path:
    """chat-demo 静态产物所在目录：打包后是 _MEIPASS/webui，源码下是 webui/。"""
    import sys

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / "webui"
        if p.is_dir():
            return p
    here = Path(__file__).resolve().parent / "webui"
    if here.is_dir():
        return here
    # 开发期兜底：直接指向 chat-demo 的构建产物
    dev = Path(r"D:\chat-demo\dist")
    return dev


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(resource_dir()), **kwargs)

    def log_message(self, *args):                    # 别把静态请求刷进控制台
        pass


class _StaticServer:
    """127.0.0.1 上的静态文件服务，端口从 8765 起自动找空位。"""

    def __init__(self) -> None:
        self._srv: socketserver.TCPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None

    def start(self) -> int | None:
        socketserver.TCPServer.allow_reuse_address = True
        for port in range(8765, 8800):
            try:
                self._srv = socketserver.TCPServer(("127.0.0.1", port), _QuietHandler)
            except OSError:
                continue
            self.port = port
            self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
            self._thread.start()
            return port
        return None

    def stop(self) -> None:
        if self._srv:
            try:
                self._srv.shutdown()
                self._srv.server_close()
            except Exception:                        # noqa: BLE001
                pass
            self._srv = None
        self.port = None


class WebPanel(QWidget):
    """内嵌 chat-demo 的聊天页。"""

    failed = Signal(str)
    # 每回合结束后报一次 token 用量：(input+output, output)。
    # 桥跑在 HTTP 线程里，必须用信号切回 Qt 线程再更新 UI。
    usage_changed = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._server = _StaticServer()
        self._view = None

        if not _WEBVIEW_OK:
            from PySide6.QtWidgets import QLabel

            msg = QLabel(
                "缺少 QtWebView 组件，无法显示聊天界面。\n\n"
                "装一下：uv pip install --system \"PySide6-Addons>=6.10\"",
                self,
            )
            msg.setWordWrap(True)
            msg.setStyleSheet("color:#f97583; padding:32px;")
            lay.addWidget(msg)
            return

        dist = resource_dir()
        if not (dist / "index.html").is_file():
            from PySide6.QtWidgets import QLabel

            msg = QLabel(
                f"找不到聊天界面文件：{dist / 'index.html'}\n\n"
                "需要先构建 chat-demo（见 docs/WEBUI.md）。",
                self,
            )
            msg.setWordWrap(True)
            msg.setStyleSheet("color:#f97583; padding:32px;")
            lay.addWidget(msg)
            return

        port = self._server.start()
        if port is None:
            from PySide6.QtWidgets import QLabel

            lay.addWidget(QLabel("本地端口被占用，聊天界面无法启动。", self))
            return

        # 启动 Anthropic→MiMo Code 的桥。chat-demo 的 API_ORIGIN 构建时
        # 指向这里，所以聊天界面发的每条消息都会走 MiMo Code，
        # 工具调用和 OCR 都在里面发生（三个引擎同时在线）。
        try:
            from core.agent_bridge import AgentBridge

            # workdir 用 MiMo Code 自己的启动目录（session 的 cwd 由它决定，
            # API 改不了），拿不到时退回当前目录。
            bridge_dir = Path.cwd()
            AgentServer_probe = None
            try:
                from core import agent_bridge as _ab

                AgentServer_probe = _ab.peek_agent()
                if AgentServer_probe is not None:
                    bridge_dir = Path(getattr(AgentServer_probe, "workdir", bridge_dir) or bridge_dir)
            except Exception:                    # noqa: BLE001
                pass
            self._bridge = AgentBridge(bridge_dir)
            self._bridge.on_usage = self._emit_usage
            bridge_port = self._bridge.start()
            if bridge_port:
                print(f"[ui_web] 桥已启动 http://127.0.0.1:{bridge_port}  cwd={bridge_dir}")
        except Exception as exc:                     # noqa: BLE001
            print(f"[ui_web] 桥启动失败：{exc}")
            self._bridge = None

        # ---------- WebView（属于 __init__，别挪到别的方法里）----------
        view = QWebView(None)
        self._view = view
        # QWebView 是 QWindow 子类（QtWebView 走 QML 那套），不能直接塞进
        # QWidget 布局；createWindowContainer 把它桥接成 QWidget。
        from PySide6.QtWidgets import QWidget

        container = QWidget.createWindowContainer(view, self)
        self._container = container
        lay.addWidget(container, 1)

        # URL 带一个版本号，绕开 WebView2 的页面缓存。
        #
        # 踩过的坑：重新构建 webui 后 index.html 引用的 JS 文件名会变
        # （main.<hash>.js）。WebView2 缓存了旧 index.html，它指向的旧 JS
        # 已经不存在 —— 请求 404，页面黑屏。而用 Playwright 打开同一个地址
        # 完全正常，因为它的缓存是干净的。
        #
        # 用 index.html 的 mtime 当版本号：构建不变 -> 地址不变 -> 缓存生效；
        # 构建变了 -> 地址变 -> 强制重新拉取。
        try:
            ver = int((dist / "index.html").stat().st_mtime)
        except Exception:                            # noqa: BLE001
            ver = 0
        view.setUrl(QUrl(f"http://127.0.0.1:{port}/?v={ver}"))

        # 构造时窗口还没有真实尺寸，先同步一次；之后靠 resizeEvent。
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self._sync_view_size)

    def _emit_usage(self, used: int, out: int) -> None:
        """桥的回调（HTTP 线程）→ 发信号（Qt 线程）。"""
        self.usage_changed.emit(used, out)

    # ---------- 尺寸同步 ----------
    # createWindowContainer 里的 QWindow**不会**自动跟随父 widget。
    # 不同步的后果不是「内容偏小」，而是**整块区域全黑**：
    # QWindow 停留在 0x0，WebView2 无从渲染。
    def _sync_view_size(self) -> None:
        cont = getattr(self, "_container", None)
        view = getattr(self, "_view", None)
        if cont is None or view is None:
            return
        # 以 container 实际拿到的区域为准（布局已经算好），不要用 self 的尺寸
        w, h = max(1, cont.width()), max(1, cont.height())
        if view.width() != w or view.height() != h:
            view.resize(w, h)

    def resizeEvent(self, event):                    # noqa: N802
        super().resizeEvent(event)
        self._sync_view_size()

    def showEvent(self, event):                      # noqa: N802
        super().showEvent(event)
        self._sync_view_size()

    # ---------- 生命周期 ----------

    def reload(self) -> None:
        if self._view is not None:
            self._view.reload()

    def shutdown(self) -> None:
        self._server.stop()
        bridge = getattr(self, "_bridge", None)
        if bridge is not None:
            try:
                bridge.stop()
            except Exception:                        # noqa: BLE001
                pass
