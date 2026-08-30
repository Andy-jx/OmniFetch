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

echo [0/4] Closing any older OmniFetch Helper...
taskkill /F /IM OmniFetchHelper.exe >nul 2>&1
timeout /t 1 /nobreak >nul

echo [1/4] Creating Windows Startup shortcut...
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

echo [2/4] Starting OmniFetch Helper v0.5.9 in background...
start "" wscript.exe "%~dp0start-helper-hidden.vbs"

timeout /t 2 /nobreak >nul

echo [3/4] Checking local helper version...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:17891/health' -TimeoutSec 4; if($r.ok -eq $true -and $r.version -eq '0.5.9'){exit 0}else{exit 2} } catch { exit 1 }"

if errorlevel 1 (
    echo.
    echo [ERROR] OmniFetch Helper v0.5.9 did not become ready.
    echo Close old OmniFetch windows, then run this installer again.
    echo Do not test downloads until the extension shows Helper v0.5.9.
    echo.
    pause
    exit /b 1
)

echo [4/4] Ready.
echo.
echo [OK] OmniFetch Helper v0.5.9 is running and Windows autostart is installed.
echo Keep the whole OmniFetch folder in its current location.
echo To remove autostart later, run uninstall-autostart.bat.
echo.
pause
exit /b 0
