"""把自检输出渲染成一张图（终端风格）。

不启动任何引擎 —— 自检本身是只读检查，纯 CPU。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

# 跑自检拿输出（去掉 ANSI 颜色码）
r = subprocess.run([sys.executable, str(ROOT / "tools" / "selfcheck.py")],
                   capture_output=True, text=True, errors="replace",
                   encoding="utf-8", cwd=str(ROOT), timeout=180)
import re
out = r.stdout or ""
out = re.sub(r"\x1b\[[0-9;]*m", "", out)
lines = out.splitlines()
print(f"自检输出 {len(lines)} 行")

C_BG = "#0f1419"
C_TXT = "#d4d4d8"
C_DIM = "#6b7280"
C_OK = "#7dd6a0"
C_WARN = "#e3b341"
C_ERR = "#f97583"
C_HEAD = "#58a6ff"

W = 1180
LINE_H = 19
TOP = 96
BOTTOM = 56
H = TOP + len(lines) * LINE_H + BOTTOM

img = QImage(W, H, QImage.Format_ARGB32)
img.fill(QColor(C_BG))
p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)
p.setRenderHint(QPainter.TextAntialiasing)

# 标题栏（模拟终端）
p.fillRect(0, 0, W, 40, QColor("#161b22"))
p.setPen(QColor(C_DIM))
p.setFont(QFont("Microsoft YaHei UI", 9))
p.drawText(16, 26, "MiMoLocal.exe --selfcheck")

mono = QFont("Consolas", 10)
mono.setStyleHint(QFont.Monospace)
p.setFont(mono)

y = TOP - 10
for ln in lines:
    if not ln.strip():
        y += LINE_H
        continue
    if ln.startswith("===="):
        color = C_DIM
    elif "[缺失]" in ln or "[FAIL]" in ln:
        color = C_ERR
    elif "[注意]" in ln:
        color = C_WARN
    elif "[OK]" in ln or "一切正常" in ln:
        color = C_OK
    elif ln.strip().endswith("项）") or "通过 " in ln:
        color = C_HEAD
    elif ln and not ln.startswith(" "):
        color = C_HEAD          # 章节标题
    else:
        color = C_TXT
    p.setPen(QColor(color))
    p.drawText(16, y, ln)
    y += LINE_H

p.end()
out_path = ROOT / "docs" / "shot-selfcheck.png"
img.save(str(out_path))
print(f"✓ {out_path.name}  {img.width()}x{img.height()}  "
      f"{out_path.stat().st_size / 1024:.0f} KB")
