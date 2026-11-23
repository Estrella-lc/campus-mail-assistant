@echo off
REM HKU Mail Widget keepalive - called by Windows Task Scheduler
REM Ensures the desktop widget is running; starts it if not.
"C:\Users\16964\.workbuddy\binaries\python\envs\default\Scripts\python.exe" "C:\Users\16964\WorkBuddy\2026-09-28-15-05-08\hku-mailbot\run_daily.py" --ensure-widget
