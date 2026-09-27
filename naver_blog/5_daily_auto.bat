@echo off
rem For Windows Task Scheduler: no pause, random start delay, log to run_log.txt
cd /d "%~dp0"
python main.py >> run_log.txt 2>&1
