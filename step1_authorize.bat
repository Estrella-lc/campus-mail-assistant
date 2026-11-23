@echo off
chcp 65001 >nul
REM Step 1 - one-time OAuth2 authorization for HKU mailbox
REM 可选参数：--profile graph --force  （补授权 Graph 通道，拿到精确邮件链接）
cd /d "%~dp0"
"C:\Users\16964\.workbuddy\binaries\python\envs\default\Scripts\python.exe" auth_login.py %*
echo.
pause
