@echo off
cd /d "%~dp0"
python install_update.py --online
echo.
pause
