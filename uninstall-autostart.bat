@echo off
setlocal EnableExtensions

title OmniFetch - Remove Autostart

set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\OmniFetch Helper.lnk"

if exist "%LINK%" (
    del /f /q "%LINK%"
)

if exist "%LINK%" (
    echo [ERROR] Failed to remove the OmniFetch Startup shortcut.
    echo.
    pause
    exit /b 1
)

echo.
echo [OK] OmniFetch Windows autostart has been removed.
echo Program files, extension files, and downloaded videos were not deleted.
echo.
pause
exit /b 0
