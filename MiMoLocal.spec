# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

import PySide6
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules('tools')

# QtWebView 的后端插件（Windows 上是 WebView2）。PyInstaller 的 PySide6 hook
# 不会自动收 plugins/webview，漏了会在运行时报 "No WebView plug-in found!"。
_webview_dir = Path(PySide6.__file__).parent / "plugins" / "webview"
_webview_datas = [
    (str(p), "PySide6/plugins/webview")
    for p in _webview_dir.glob("*.dll")
] if _webview_dir.is_dir() else []
print(f"[spec] WebView 插件 {len(_webview_datas)} 个")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('VERSION.txt', '.'),
        ('assets/app.ico', '.'),
        ('assets/app.png', '.'),
        # 聊天前端（chat-demo 的构建产物，MIT）。运行期由 ui_web.py
        # 用 127.0.0.1 上的静态服务托管，所以必须带在包里。
        ('webui', 'webui'),
        *_webview_datas,
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # QtWebView 是聊天界面的渲染层，不能排除；
    # 其余大件（numpy/PIL/QtQml 等）继续排除以控制体积。
    excludes=['numpy', 'PIL', 'matplotlib', 'pandas', 'scipy', 'pytest', 'PySide6.Qt3DCore', 'PySide6.QtMultimedia'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MiMoLocal',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/app.ico'],
)
