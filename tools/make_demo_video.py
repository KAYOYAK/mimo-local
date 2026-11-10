"""生成演示动画（GIF + MP4）。

时间线（约 8 秒）：
  0.0s  空界面
  0.3s  开始打字「这张收据一共多少钱？买了什么？」
  1.8s  点「图片」，待发送区出现缩略图
  2.6s  点「发送」→ 用户消息上屏，出现图片块「识别中…」
  3.2s  OCR 完成，缩略图和折叠的识别原文出现
  3.8s  思考过程出现（折叠状态）
  4.2s  回答逐字流式出现
  7.0s  停留

    python tools/make_demo_video.py

产物：
    docs/demo.gif      12fps，README 内嵌用
    docs/demo.mp4      30fps，release 附件用
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
import ui_qt

FPS = 12
OUT_W, OUT_H = 1440, 920
GIF_W = 1200        # GIF 用缩小的尺寸：1440 宽用 256 色调色板压缩后文字会糊

SESSIONS = [
    {"id": "ses_1", "title": "收据解析：MR. D.I.Y.", "titleRevision": 1,
     "time": {"updated": 1794060000000}},
    {"id": "ses_2", "title": "写一个快速排序", "titleRevision": 1,
     "time": {"updated": 1794050000000}},
    {"id": "ses_3", "title": "项目里有哪些文件", "titleRevision": 1,
     "time": {"updated": 1794040000000}},
]

QUESTION = "这张收据一共多少钱？买了什么？"

OCR_TEXT = """MR. D.I.Y. SDN BHD
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

ANSWER = ("这是一张 MR. D.I.Y. 的税务发票（Cheras Leisure Mall）：\n\n"
          "**一共 RM 5.90**\n"
          "• 商品：CLOTH PEG E-140 *S，数量 1，单价 RM 5.90\n"
          "• 含税前净额 RM 5.57，GST 6% 是 RM 0.33\n"
          "• 支付方式：现金")

REASONING = "这是一张收据，OCR 已经转成文字了。用户问总价和买了什么。\n从文字里找 Total 行和商品行……"

frames: list = []


def build_window():
    w = ui_qt.MainWindow()
    w.resize(OUT_W, OUT_H)
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
    return w


def capture(w, n: int = 1):
    """连续抓 n 帧（内容不变时用来拖时间）。"""
    for _ in range(n):
        w._dirty = True
        w._flush()
        QApplication.processEvents()
        frames.append(w.grab().toImage())


def main() -> int:
    w = build_window()

    # 0.0-0.3s 空界面
    capture(w, 4)

    # 0.3-1.8s 逐字打字
    step = 2
    for i in range(step, len(QUESTION) + 1, step):
        w.input.setPlainText(QUESTION[:i])
        capture(w, 1)
    w.input.setPlainText(QUESTION)
    capture(w, 2)

    # 1.8-2.6s 选图，待发送区出现缩略图
    img = ROOT / "docs" / "测试图.jpg"
    w._pending_images.append(img)
    w._refresh_image_bar()
    capture(w, 4)

    # 2.6-3.2s 发送：用户消息上屏 + 图片块「识别中…」
    w.input.clear()
    w._pending_images.clear()
    w._refresh_image_bar()

    u = ui_qt.Item("", "text", role="user")
    u.text = QUESTION
    w.items.append(u)

    oi = ui_qt.Item("", "ocr")
    oi.tool = img.name
    oi.text = "识别中…"
    oi._pending = img
    w.items.append(oi)
    w._set_status("识别图片", "busy")
    capture(w, 5)

    # 3.2-3.8s OCR 完成
    oi.text = OCR_TEXT
    w._set_status("思考中", "busy")
    capture(w, 5)

    # 3.8-4.2s 思考过程
    r = ui_qt.Item("rs1", "reasoning")
    r.text = REASONING
    w.items.append(r)
    capture(w, 4)

    # 4.2-7.0s 回答逐字流式
    a = ui_qt.Item("ai1", "text")
    a.text = ""
    w.items.append(a)
    chunk = 8
    for i in range(chunk, len(ANSWER) + 1, chunk):
        a.text = ANSWER[:i]
        capture(w, 1)
    a.text = ANSWER
    w._set_status("就绪", "ok")
    capture(w, 6)

    total = len(frames)
    print(f"共 {total} 帧（{total / FPS:.1f} 秒 @ {FPS}fps）")

    # ---- 保存 PNG 序列 ----
    tmp = ROOT / "_frames"
    tmp.mkdir(exist_ok=True)
    from PIL import Image
    pils = []
    for i, im in enumerate(frames):
        im2 = im.scaled(OUT_W, OUT_H)
        buf = im2.bits()
        # QImage → PIL
        from PySide6.QtGui import QImage
        im3 = im2.convertToFormat(QImage.Format_RGB888)
        p = Image.frombytes("RGB", (im3.width(), im3.height()),
                            bytes(im3.constBits()))
        pils.append(p)
    print(f"转成 PIL {len(pils)} 帧")

    # ---- GIF（缩小尺寸 + 更好的量化）----
    from PIL import Image as PILImage
    gif_h = int(GIF_W * OUT_H / OUT_W)
    small = [p.resize((GIF_W, gif_h), PILImage.LANCZOS) for p in pils]
    # 先统一转成自适应调色板，减少帧间抖动
    quantized = [p.convert("P", palette=PILImage.ADAPTIVE, colors=128) for p in small]
    gif_path = ROOT / "docs" / "demo.gif"
    quantized[0].save(
        str(gif_path), save_all=True, append_images=quantized[1:],
        duration=int(1000 / FPS), loop=0, optimize=True, disposal=2,
    )
    print(f"  ✓ demo.gif   {gif_path.stat().st_size / 1024:.0f} KB")

    # ---- MP4（用 imageio-ffmpeg）----
    import imageio.v2 as iio
    mp4_path = ROOT / "docs" / "demo.mp4"
    writer = iio.get_writer(str(mp4_path), fps=30, codec="libx264",
                            quality=8, pixelformat="yuv420p")
    import numpy as np
    for p in pils:
        arr = np.asarray(p)
        # 每帧写两次凑 24fps 观感（GIF 帧率低，MP4 补上）
        writer.append_data(arr)
        writer.append_data(arr)
    writer.close()
    print(f"  ✓ demo.mp4   {mp4_path.stat().st_size / 1024:.0f} KB")

    # 清理
    w.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
