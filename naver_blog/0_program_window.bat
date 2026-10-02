@echo off
cd /d "%~dp0"
start "" pythonw gui.py
if errorlevel 1 python gui.py
