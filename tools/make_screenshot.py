"""生成 README 用的功能展示截图。

场景：用户发一张收据图 + 问「一共多少钱？买了什么？」
      → 界面跑 OCR → 缩略图 + 折叠的识别原文 → 模型基于全文回答

内容用的是真实跑出来的 OCR 和回答（不是编的），只是通过 Qt 渲染，
不真的启动引擎 —— 这样截图可控、可重复。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
import ui_qt

W, H = 1440, 920
w = ui_qt.MainWindow()
w.resize(W, H)

# 假的项目目录（别暴露真实路径）
w.proj_label.setText(r"D:\projects\demo")
w.lbl_dir.setText("demo")
w.workdir = Path(r"D:\projects\demo")
# 顶栏做成「已启动」的样子
w.btn_engine.setText("停止引擎")

# 左边对话列表
fake_sessions = [
    {"id": "ses_1", "title": "收据解析：MR. D.I.Y.", "titleRevision": 1,
     "time": {"updated": 1794060000000}},
    {"id": "ses_2", "title": "改进界面布局", "titleRevision": 1,
     "time": {"updated": 1794050000000}},
    {"id": "ses_3", "title": "修复端口占用问题", "titleRevision": 1,
     "time": {"updated": 1794040000000}},
]
w._fill_sessions(fake_sessions)

# 底部状态
w.ctx_label.setText("上下文 64K\n9B + OCR 在线\n全本地推理")
w._set_status("就绪", "ok")

# ---- 对话内容 ----
w.items.clear()
w._by_id.clear()
w._echoes.clear()
w._seen.clear()
w._expanded.clear()

# 1. 用户消息
u = ui_qt.Item("", "text", role="user")
u.text = "这张收据一共多少钱？买了什么？"
w.items.append(u)

# 2. 图片块（缩略图 + 折叠的 OCR 原文）
img = ROOT / "docs" / "测试图.jpg"
ocr_text = """MR. D.I.Y. SDN BHD
(CO.REG :704427-T )
LOT 1851-A & 1851-B, JALAN KPB 6,
KAWASAN PERINDUSTRIAN BALAKONG,
43300 SERI KEMBANGAN, SELANGOR
(GST ID No :001092886528)
(CHERAS LEISURE MALL)
-TAX INVOICE-

CLOTH PEG E-140                     *S
WA36 - 6/60
9555112901406            1 X  5.90   5.90

Item(s) : 1                  Qty(s) : 1
------------------------------------------
Total Incl. GST@6%                RM 5.90
CASH                              RM 5.90

GST Summary          Amt(RM)    Tax(RM)
GST @6%                 5.57        0.33"""

oi = ui_qt.Item("", "ocr")
oi.tool = img.name
oi.text = ocr_text
oi._pending = img
w.items.append(oi)

# 3. 折叠的思考过程
r = ui_qt.Item("r1", "reasoning")
r.text = "这是一张收据，OCR 已经转成文字了。用户问总价和买了什么。\n从文字里找 Total 行和商品行……"
w.items.append(r)

# 4. AI 回答（真实跑出来的内容）
a = ui_qt.Item("a1", "text")
a.text = ("这是一张 MR. D.I.Y. 的税务发票（Cheras Leisure Mall）：\n\n"
          "**一共 RM 5.90**\n"
          "• 商品：CLOTH PEG E-140 *S，数量 1，单价 RM 5.90\n"
          "• 含税前净额 RM 5.57，GST 6% 是 RM 0.33\n"
          "• 支付方式：现金")
w.items.append(a)

w._dirty = True
w._flush()
w.chat.verticalScrollBar().setValue(0)
QApplication.processEvents()

# 截图
pm = w.grab()
out = ROOT / "docs" / "screenshot.png"
pm.save(str(out))
print(f"截图: {out}  {pm.width()}x{pm.height()}  {out.stat().st_size} 字节")
