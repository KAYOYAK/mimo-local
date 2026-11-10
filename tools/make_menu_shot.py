"""生成「⋯ 工具菜单」展示图。

主窗口截图 + 菜单截图合成 —— 菜单是独立弹出窗口，grab 主窗口截不到，
所以要分别 grab 再叠上去。
"""
import os
import sys
from pathlib import Path

# 这台机器是 150% 缩放。不禁用的话：
#   - w.grab() 返回物理像素 2160x1380
#   - 但内容只画在左上角 960x613（逻辑尺寸又除以了 1.5）
# 三方尺寸对不上，合成出来的图四周全黑。直接关掉缩放最省心。
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
os.environ["QT_SCALE_FACTOR"] = "1"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
import ui_qt

W, H = 1440, 920

SESSIONS = [
    {"id": "ses_1", "title": "收据解析：MR. D.I.Y.", "titleRevision": 1,
     "time": {"updated": 1794060000000}},
    {"id": "ses_2", "title": "写一个快速排序", "titleRevision": 1,
     "time": {"updated": 1794050000000}},
]

w = ui_qt.MainWindow()
w.resize(W, H)
w.proj_label.setText(r"D:\projects\demo")
w.lbl_dir.setText("demo")
w.workdir = Path(r"D:\projects\demo")
w.btn_engine.setText("停止引擎")
w._fill_sessions(SESSIONS)
w.ctx_label.setText("上下文 64K\n9B + OCR 在线\n全本地推理")
w._set_status("就绪", "ok")

w.items.clear()
w._by_id.clear()
w._echoes.clear()
w._seen.clear()
w._expanded.clear()

u = ui_qt.Item("", "text", role="user")
u.text = "这个项目用了哪些技术栈？"
w.items.append(u)

r = ui_qt.Item("rs1", "reasoning")
r.text = "先看看 README 和依赖文件。"
w.items.append(r)

a = ui_qt.Item("ai1", "text")
a.text = ("项目分三块：\n\n"
          "• **界面**：PySide6 + QTextBrowser 渲染\n"
          "• **引擎**：llama.cpp 起两个 server（8080 / 8090）\n"
          "• **Agent**：MiMo Code 跑在 4096，通过 HTTP 驱动\n\n"
          "关键是三个同时在线，不是切换。")
w.items.append(a)

w._dirty = True
w._flush()
w.show()
for _ in range(8):
    QApplication.processEvents()
    w.repaint()
QApplication.processEvents()

# ---- 主窗口 ----
# 150% DPI 下 w.grab() 返回 2160x1380，但实际内容只画在左上角
# 1440x920 那块（Qt 的 grab 没跟着缩放）。所以**裁剪**左上角，
# 不是缩放整张图 —— 缩放会把内容压成 2/3 大小，四周全是黑的。
main_img = w.grab().toImage()
print(f"grab 尺寸 {main_img.width()}x{main_img.height()}")
if main_img.width() > W or main_img.height() > H:
    main_img = main_img.copy(0, 0, min(W, main_img.width()), min(H, main_img.height()))

# ---- 菜单 ----
menu = w._tools_menu
# 定位到按钮下方
import ui_qt as _u
btn_pos = w.btn_tools.mapToGlobal(QPoint(0, w.btn_tools.height()))
btn_x = w.btn_tools.mapTo(w, QPoint(0, 0)).x()
btn_y = w.btn_tools.mapTo(w, QPoint(0, 0)).y() + w.btn_tools.height()

menu.adjustSize()
menu.popup(btn_pos)
QApplication.processEvents()
menu_img = menu.grab().toImage()
menu.hide()

print(f"主窗口 {main_img.width()}x{main_img.height()}")
print(f"菜单   {menu_img.width()}x{menu_img.height()}")

# ---- 合成 ----
canvas = QImage(W, H, QImage.Format_ARGB32)
canvas.fill(QColor("#000000"))
p = QPainter(canvas)
p.drawImage(0, 0, main_img)

# 菜单右对齐到按钮右边（和真实弹出位置一致）
mh = menu_img.height()
mx = min(btn_x + w.btn_tools.width() - menu_img.width(), W - menu_img.width() - 20)
my = min(btn_y + 4, H - mh - 20)

# 阴影
for i in range(6, 0, -1):
    p.setBrush(QColor(0, 0, 0, 12))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(mx - i, my - i + 2,
                      menu_img.width() + i * 2, mh + i * 2, 10, 10)
p.drawImage(mx, my, menu_img)
p.end()

out = ROOT / "docs" / "shot-menu.png"
canvas.save(str(out))
print(f"✓ {out.name}  {out.stat().st_size / 1024:.0f} KB")

# 不调 w.close()：它触发 closeEvent 的异步清理线程，而脚本马上退出，
# 线程里 self.bridge.quit.emit() 会报 "Signal source has been deleted"。
# 让进程自己退，Qt 会清理。
