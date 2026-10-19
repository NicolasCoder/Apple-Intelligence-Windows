@echo off
cd /d "%~dp0Windows_and_Linux"
if exist "start_silent.vbs" (
    wscript.exe //B //Nologo "start_silent.vbs"
) else (
    start "" pythonw.exe main.py
)
