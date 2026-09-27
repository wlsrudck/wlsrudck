@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
python -m playwright install chromium
echo.
echo Done. Next: 2_login.bat
pause
