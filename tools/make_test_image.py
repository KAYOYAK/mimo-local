"""生成一张小测试图（不依赖 Pillow，手写 PNG）。

用途：verify_packaged.ps1 里验证 ``--ocr`` 子命令真的能产出 .md。
图片内容是几个黑色矩形 + 文字形状的像素，足够 OCR 引擎跑起来。
文件控制在几 KB，直接进仓库。
"""
import struct
import sys
import zlib
from pathlib import Path


def make_png(path: Path, w: int = 320, h: int = 120) -> None:
    # 白底
    px = [[255, 255, 255] for _ in range(w * h)]

    def rect(x0, y0, x1, y1, v=0):
        for y in range(max(0, y0), min(h, y1)):
            for x in range(max(0, x0), min(w, x1)):
                px[y * w + x] = [v, v, v]

    # 几条横线模拟文字行
    for i, (y, hh, line_w) in enumerate([(20, 12, 240), (50, 12, 200), (80, 12, 260)]):
        rect(30, y, 30 + line_w, y + hh)
    # 一个方块
    rect(280, 15, 310, 45, 60)

    raw = b""
    for y in range(h):
        raw += b"\x00"      # filter type 0
        for x in range(w):
            r, g, b = px[y * w + x]
            raw += bytes((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)   # 8bit RGB
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/测试图.png")
    make_png(out)
    print(f"已生成 {out}  ({out.stat().st_size} 字节)")
