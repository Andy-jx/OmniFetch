@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title OmniFetch - Install Autostart

if not exist "OmniFetchHelper.exe" (
    echo [ERROR] OmniFetchHelper.exe was not found in this folder.
    echo Please run this script from the extracted OmniFetch Windows package.
    echo.
    pause
    exit /b 1
)

if not exist "start-helper-hidden.vbs" (
    echo [ERROR] start-helper-hidden.vbs was not found in this folder.
    echo.
    pause
    exit /b 1
)

set "OMNIFETCH_DIR=%~dp0"
set "OMNIFETCH_EXE=%~dp0OmniFetchHelper.exe"
set "OMNIFETCH_VBS=%~dp0start-helper-hidden.vbs"
set "OMNIFETCH_LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\OmniFetch Helper.lnk"

echo [1/3] Creating Windows Startup shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut($env:OMNIFETCH_LINK); $s.TargetPath=Join-Path $env:SystemRoot 'System32\wscript.exe'; $s.Arguments=[char]34+$env:OMNIFETCH_VBS+[char]34; $s.WorkingDirectory=$env:OMNIFETCH_DIR; $s.IconLocation=$env:OMNIFETCH_EXE+',0'; $s.Save()"

if errorlevel 1 (
    echo [ERROR] Failed to create the Windows Startup shortcut.
    echo.
    pause
    exit /b 1
)

if not exist "%OMNIFETCH_LINK%" (
    echo [ERROR] Startup shortcut was not created.
    echo.
    pause
    exit /b 1
)

echo [2/3] Starting OmniFetch Helper in background...
start "" wscript.exe "%~dp0start-helper-hidden.vbs"

timeout /t 2 /nobreak >nul

echo [3/3] Checking local helper...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:17891/health' -TimeoutSec 3; if($r.ok -eq $true){exit 0}else{exit 1} } catch { exit 1 }"

if errorlevel 1 (
    echo.
    echo [WARNING] Autostart was installed, but the helper health check did not respond yet.
    echo You can still reboot Windows or double-click run-helper.bat to test it.
) else (
    echo.
    echo [OK] OmniFetch Helper is running and Windows autostart is installed.
)

echo.
echo Keep the whole OmniFetch folder in its current location.
echo To remove autostart later, run uninstall-autostart.bat.
echo.
pause
exit /b 0
