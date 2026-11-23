@echo off
REM Step 2 - start the desktop widget (Electron)
set "ELECTRON_RUN_AS_NODE="
cd /d "%~dp0widget"
start "" "node_modules\electron\dist\electron.exe" .
exit
