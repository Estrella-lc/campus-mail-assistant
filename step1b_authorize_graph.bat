@echo off
chcp 65001 >nul
REM 补授权：Microsoft Graph 通道 —— 让「打开邮件」按钮直达那一封邮件（约 1 分钟）
REM 会先自动备份现有 IMAP 凭证；即便本步失败，收信功能也不受影响。
cd /d "%~dp0"
echo [1/2] 备份现有凭证 ...
copy /Y "data\token_cache.json" "data\token_cache.imap.bak" >nul 2>&1
echo [2/2] 发起 Graph 授权（浏览器会自动打开，设备码已复制到剪贴板）...
echo.
"C:\Users\16964\.workbuddy\binaries\python\envs\default\Scripts\python.exe" auth_login.py --profile graph --force --codes 3
echo.
pause
