"""从 SVG 生成 Windows 应用图标（多尺寸 .ico + 窗口图标 PNG）。

图标来源：Lucide Icons 的 `square-terminal`（ISC 许可）
  https://lucide.dev/icons/square-terminal
源文件在 assets/square-terminal.svg，改图标就换这个文件再跑一遍：

    python tools/make_icon.py

产物：
    assets/app.ico   9 个尺寸（16/20/24/32/40/48/64/128/256）
    assets/app.png   256x256，给窗口图标用

设计说明：橙色圆角底 + 白色图形，而不是纯线条。
纯线条在 16x16 会糊成一团；橙底白图在任何尺寸和背景下都清晰，
也是 Windows 应用图标的常见做法。

ICO 格式（Vista+ 支持内嵌 PNG）：
    ICONDIR      6 字节（reserved, type=1, count）
    ICONDIRENTRY 16 字节 × N
    数据         PNG 字节流
"""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ACCENT = "#ff6a2b"
SRC = ROOT / "assets" / "square-terminal.svg"
OUT_ICO = ROOT / "assets" / "app.ico"
OUT_PNG = ROOT / "assets" / "app.png"

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    """渲染成 size×size 的透明 PNG（橙底白图），返回字节。"""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)

    # 橙色圆角方块
    p.setBrush(QColor(ACCENT))
    p.setPen(Qt.NoPen)
    rad = size * 0.22
    p.drawRoundedRect(QRectF(0, 0, size, size), rad, rad)

    # 白色图形。小尺寸少留白，否则符号会小到看不见
    margin = size * (0.24 if size <= 32 else 0.20)
    renderer.render(p, QRectF(margin, margin, size - 2 * margin, size - 2 * margin))
    p.end()

    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def main() -> int:
    if not SRC.is_file():
        print(f"缺源文件：{SRC}", file=sys.stderr)
        return 1

    app = QApplication.instance() or QApplication(sys.argv)

    svg_text = SRC.read_text(encoding="utf-8").replace("currentColor", "#ffffff")
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    if not renderer.isValid():
        print(f"SVG 无效：{SRC}", file=sys.stderr)
        return 1

    print("渲染（橙底白图）…")
    blobs = []
    for s in SIZES:
        data = render_png(renderer, s)
        blobs.append((s, data))
        print(f"  {s}x{s}: {len(data)} 字节")

    # ---- 拼 ICO ----
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = b""
    for s, data in blobs:
        b = 0 if s >= 256 else s        # 256 在 ICO 里写 0
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(data), offset)
        offset += len(data)

    OUT_ICO.parent.mkdir(parents=True, exist_ok=True)
    with OUT_ICO.open("wb") as f:
        f.write(header + entries)
        for _, data in blobs:
            f.write(data)

    print(f"\n✓ {OUT_ICO.relative_to(ROOT)}  {OUT_ICO.stat().st_size} 字节"
          f"  （{len(blobs)} 个尺寸）")

    OUT_PNG.write_bytes(dict(blobs)[256])
    print(f"✓ {OUT_PNG.relative_to(ROOT)}  {OUT_PNG.stat().st_size} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
