"""启动程序并截图，检查窗口左上角图标。"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\mimo-desktop")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QGuiApplication

app = QApplication(sys.argv)

exe = Path(r"C:\Users\XSZ20\AppData\Local\MiMoLocal\MiMoLocal.exe")
p = subprocess.Popen([str(exe)])
print(f"启动 PID {p.pid}")
time.sleep(7)

try:
    screen = QGuiApplication.primaryScreen()
    pm = screen.grabWindow(0)
    out = Path(r"D:\mimo-desktop\_screen.png")
    pm.save(str(out))
    print(f"全屏截图 {pm.width()}x{pm.height()} → {out}")
finally:
    p.terminate()
    p.wait(timeout=10)
    print("已关闭")
