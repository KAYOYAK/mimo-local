@echo off
rem  MiMo Desktop launcher.
rem  Must stay pure ASCII - cmd.exe reads .bat in the OEM code page.
rem  pythonw.exe = Python without a console window (no black box).
cd /d "%~dp0"
start "" pythonw.exe "%~dp0main.py"
