@echo off
REM Step 3 - run the daily mail digest manually (fetch + analyze + notify)
cd /d "%~dp0"
"C:\Users\16964\.workbuddy\binaries\python\envs\default\Scripts\python.exe" run_daily.py --notify
echo.
pause
