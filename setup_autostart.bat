@echo off
REM Install: launch the widget automatically at Windows startup (creates a shortcut in the Startup folder)
set "WIDGET_DIR=%~dp0widget"
set "EXE=%WIDGET_DIR%\node_modules\electron\dist\electron.exe"
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\HKU Mail Widget.lnk"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%'); $s.TargetPath='%EXE%'; $s.Arguments='.'; $s.WorkingDirectory='%WIDGET_DIR%'; $s.WindowStyle=7; $s.Description='HKU Mail Assistant Desktop Widget'; $s.Save()"
if exist "%LNK%" (echo [OK] Startup shortcut created) else (echo [FAIL] Could not create shortcut)
echo To undo: delete the file below
echo %LNK%
pause
