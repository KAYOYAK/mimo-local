"""
MiMo Local 安装器 —— 打包成一个独立的 Setup.exe。

为什么不用 Inno Setup：那台机器下载不动（0.13 MB/分钟）。
自己写一个功能一样：双击 → 复制程序 → 建桌面快捷方式 → 启动。

安装位置：%LOCALAPPDATA%\\MiMoLocal（不需要管理员权限）
卸载：控制面板里会有一条（因为写了注册表卸载项），或者直接删目录
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

APP_NAME = "MiMo Local"
APP_EXE = "MiMoLocal.exe"


def _no_window() -> dict:
    """Windows 上别弹黑窗口。"""
    if sys.platform == "win32":
        return {
            "creationflags": subprocess.CREATE_NO_WINDOW,
            "startupinfo": subprocess.STARTUPINFO(
                dwFlags=subprocess.STARTF_USESHOWWINDOW,
                wShowWindow=subprocess.SW_HIDE,
            ),
        }
    return {}


def _read_version() -> str:
    """从 config.py 里解析版本号，而不是再写一份。

    之前这里写死 VERSION = "1.0.0"，而 config.py 是 1.0.2 —— 安装器界面和
    「应用和功能」里的 DisplayVersion 会写成旧版本。版本号只能有一个来源。

    不 import config：安装器是独立打包的，不该为了一个版本号把 PySide6
    那一串依赖拉进来。
    """
    import re
    try:
        src = (Path(__file__).resolve().parent / "config.py").read_text(encoding="utf-8")
        m = re.search(r'^VERSION\s*=\s*"([^"]+)"', src, re.M)
        if m:
            return m.group(1)
    except Exception:
        pass
    # 打包后 config.py 在 exe 里，读不到 —— 退回到构建时写进来的值
    try:
        bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "VERSION.txt"
        if bundled.is_file():
            return bundled.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return "0.0.0"


VERSION = _read_version()

INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "MiMoLocal"


def payload_path() -> Path | None:
    """找到被打包进来的 MiMoLocal.exe。"""
    # PyInstaller onefile：数据解压到 sys._MEIPASS
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for cand in (base / APP_EXE, base / "dist" / APP_EXE, Path(__file__).resolve().parent / "dist" / APP_EXE):
        if cand.is_file():
            return cand
    return None


def agents_path() -> Path | None:
    """找到被打包进来的 AGENTS.md（OCR 工具说明）。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for cand in (base / "AGENTS.md", Path(__file__).resolve().parent / "AGENTS.md"):
        if cand.is_file():
            return cand
    return None


def make_shortcut(lnk: Path, target: Path) -> bool:
    """用 WScript.Shell 建 .lnk（Python 标准库做不了这个）。"""
    ps = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        f"$s.TargetPath = '{target}';"
        f"$s.WorkingDirectory = '{target.parent}';"
        f"$s.IconLocation = '{target},0';"
        f"$s.Description = '{APP_NAME}';"
        f"$s.Save()"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=30,
        )
        return r.returncode == 0 and lnk.exists()
    except Exception:
        return False


def refresh_icon_cache() -> None:
    """让 Windows 重新读快捷方式的图标。

    不删 IconCache.db（那要重启 explorer、会闪屏），用 ie4uinit -show：
    这是 Windows 自带的「刷新图标缓存」入口，代价小、不打扰用户。
    失败就算了 —— 图标晚一点自己会更新。
    """
    try:
        subprocess.run(["ie4uinit.exe", "-show"], capture_output=True, timeout=10,
                       **_no_window())
    except Exception:
        pass


def write_uninstall_script() -> Path:
    """写卸载脚本（打包成 exe 太麻烦，用 cmd 足够）。

    必须和 write_uninstaller() 里注册表指向的**同一个文件名**。
    之前这里是类方法，静默安装路径没调用它 → 注册表指向一个不存在的
    卸载.cmd，「应用和功能」里点了没反应。
    """
    uninst = INSTALL_DIR / "卸载.cmd"
    uninst.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        f"echo 正在卸载 {APP_NAME}...\r\n"
        f"taskkill /F /IM {APP_EXE} >nul 2>&1\r\n"
        "timeout /t 1 >nul\r\n"
        f'rd /s /q "{INSTALL_DIR}"\r\n'
        f'reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{APP_NAME}" /f >nul 2>&1\r\n'
        f'del /q "%USERPROFILE%\\Desktop\\{APP_NAME}.lnk" >nul 2>&1\r\n'
        f'del /q "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\{APP_NAME}.lnk" >nul 2>&1\r\n'
        f"echo 已卸载。\r\n"
        "pause\r\n",
        encoding="utf-8",
    )
    return uninst


def write_uninstaller() -> bool:
    """写注册表卸载项，让它在「应用和功能」里看得到。

    注意 UninstallString 必须指向**真实存在**的文件，而且 .cmd 要用
    `cmd /c` 包一层，否则在「应用和功能」里点了没反应。
    """
    try:
        import winreg
        # 实际写出的是 .cmd，注册表也必须指它
        uninst = INSTALL_DIR / "卸载.cmd"
        key_path = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
            winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, str(INSTALL_DIR))
            winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, str(INSTALL_DIR / APP_EXE))
            winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, "KAYOYAK")
            winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ,
                              f'cmd /c "{uninst}"')
        return True
    except Exception:
        return False


class Installer:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"{APP_NAME} 安装")
        root.geometry("520x340")
        root.resizable(False, False)
        root.configure(bg="#181818")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        tk.Label(root, text=APP_NAME, bg="#181818", fg="#f4f4f5",
                 font=("Microsoft YaHei UI", 18, "bold")).pack(pady=(28, 4))
        tk.Label(root, text="纯本地 AI 编程助手 · 版本 " + VERSION,
                 bg="#181818", fg="#86868b", font=("Microsoft YaHei UI", 9)).pack()

        info = tk.Frame(root, bg="#1f1f1f")
        info.pack(fill=tk.X, padx=28, pady=20)

        tk.Label(info, text="安装到：", bg="#1f1f1f", fg="#86868b",
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(fill=tk.X, padx=14, pady=(12, 2))
        tk.Label(info, text=str(INSTALL_DIR), bg="#1f1f1f", fg="#f4f4f5",
                 font=("Consolas", 9), anchor="w", wraplength=440,
                 justify=tk.LEFT).pack(fill=tk.X, padx=14)

        self.chk_desktop = tk.BooleanVar(value=True)
        tk.Checkbutton(info, text="创建桌面快捷方式", variable=self.chk_desktop,
                       bg="#1f1f1f", fg="#f4f4f5", selectcolor="#2e2e2e",
                       activebackground="#1f1f1f", activeforeground="#f4f4f5",
                       font=("Microsoft YaHei UI", 9), bd=0,
                       highlightthickness=0).pack(anchor="w", padx=14, pady=(10, 12))

        self.status = tk.Label(root, text="", bg="#181818", fg="#86868b",
                               font=("Microsoft YaHei UI", 9))
        self.status.pack()

        self.btn = tk.Button(root, text="安装", command=self.do_install,
                             bg="#ff6a2b", fg="#12100e", relief=tk.FLAT, bd=0,
                             font=("Microsoft YaHei UI", 10, "bold"),
                             padx=40, pady=9, cursor="hand2",
                             activebackground="#ff8352", activeforeground="#12100e")
        self.btn.pack(pady=(6, 20))

    def say(self, t: str, color="#86868b"):
        self.status.configure(text=t, fg=color)
        self.root.update_idletasks()

    def do_install(self):
        self.btn.configure(state=tk.DISABLED)
        try:
            self._install()
        except Exception as e:
            self.say(f"安装失败：{e}", "#ff9b7f")
            messagebox.showerror("安装失败", str(e))
            self.btn.configure(state=tk.NORMAL)

    def _install(self):
        src = payload_path()
        if src is None:
            raise RuntimeError(f"安装包里找不到 {APP_EXE}")

        self.say("正在创建目录…")
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)

        self.say("正在复制程序…")
        target = INSTALL_DIR / APP_EXE
        # 目标可能在运行，先试着关掉
        subprocess.run(["taskkill", "/F", "/IM", APP_EXE],
                       capture_output=True, timeout=10)
        shutil.copy2(src, target)

        a = agents_path()
        if a:
            shutil.copy2(a, INSTALL_DIR / "AGENTS.md")
        (INSTALL_DIR / "logs").mkdir(exist_ok=True)

        # 卸载器 = 把自己复制一份，运行时删目录
        self._write_uninstaller(target)

        if self.chk_desktop.get():
            self.say("正在创建桌面快捷方式…")
            desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
            make_shortcut(desktop / f"{APP_NAME}.lnk", target)

        self.say("正在注册开始菜单…")
        start = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        make_shortcut(start / f"{APP_NAME}.lnk", target)

        self.say("正在写注册表…")
        write_uninstaller()

        self.say("安装完成", "#4ade80")
        self.btn.configure(text="启动", state=tk.NORMAL, command=lambda: self._launch(target))
        if messagebox.askyesno("安装完成", f"{APP_NAME} 已安装。\n\n现在启动吗？"):
            self._launch(target)

    def _write_uninstaller(self, app_exe: Path):
        write_uninstall_script()

    def _launch(self, target: Path):
        try:
            subprocess.Popen([str(target)], cwd=str(target.parent))
        except Exception as e:
            messagebox.showerror("启动失败", str(e))
        self.on_close()

    def on_close(self):
        self.root.destroy()


def silent_install() -> int:
    """命令行静默安装：Setup.exe --silent"""
    try:
        src = payload_path()
        if src is None:
            print(f"找不到 {APP_EXE}", file=sys.stderr)
            return 1
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        target = INSTALL_DIR / APP_EXE
        subprocess.run(["taskkill", "/F", "/IM", APP_EXE], capture_output=True, timeout=10)
        shutil.copy2(src, target)
        print(f"已复制 -> {target}")

        # AGENTS.md 一起装（某些条情况下 config 会 fallback，但装一份更稳）
        a = agents_path()
        if a:
            shutil.copy2(a, INSTALL_DIR / "AGENTS.md")
            print("已复制 AGENTS.md")

        (INSTALL_DIR / "logs").mkdir(exist_ok=True)

        # 卸载脚本必须真的写出来，否则注册表那一条点了没反应
        uninst = write_uninstall_script()
        print(f"卸载脚本: [OK] {uninst.name}")

        desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
        ok_d = make_shortcut(desktop / f"{APP_NAME}.lnk", target)
        print(f"桌面快捷方式: {'[OK]' if ok_d else '[FAIL]'} {desktop / (APP_NAME + '.lnk')}")

        start = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        ok_s = make_shortcut(start / f"{APP_NAME}.lnk", target)
        print(f"开始菜单: {'[OK]' if ok_s else '[FAIL]'}")

        print(f"注册表: {'[OK]' if write_uninstaller() else '[FAIL]'}")

        # 刷新 Windows 图标缓存。
        # 快捷方式其实一直指向 MiMoLocal.exe、exe 里也有图标，但 Windows
        # 会缓存旧图标 —— 装完不刷的话桌面和开始菜单还是上一个版本的图标
        #（实测见过显示成 Python 图标的情况）。
        refresh_icon_cache()
        print("图标缓存: 已刷新")
        return 0
    except Exception as e:
        print(f"失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if "--silent" in sys.argv:
        sys.exit(silent_install())
    root = tk.Tk()
    Installer(root)
    root.mainloop()
