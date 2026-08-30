@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

title OmniFetch Local Helper

if not exist "OmniFetchHelper.exe" (
    echo [错误] 当前目录没有 OmniFetchHelper.exe。
    echo 如果你下载的是源码版，请运行 install-helper.bat 和 run-helper.bat。
    echo.
    pause
    exit /b 1
)

OmniFetchHelper.exe
set "EXIT_CODE=%errorlevel%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [错误] OmniFetchHelper.exe 异常退出，错误码：%EXIT_CODE%
    pause
)
exit /b %EXIT_CODE%
