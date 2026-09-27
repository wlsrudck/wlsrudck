@echo off
cd /d "%~dp0"
python main.py --dry-run
echo.
echo Check the "output" folder.
pause
