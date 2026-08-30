@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

title OmniFetch Local Helper

echo ============================================================
echo OmniFetch - 本地下载助手
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 尚未安装本地助手环境。
    echo 请先双击 install-helper.bat。
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "helper\server_v2.py"
set "EXIT_CODE=%errorlevel%"

echo.
if not "%EXIT_CODE%"=="0" (
    echo [错误] OmniFetch 本地助手异常退出，错误码：%EXIT_CODE%
) else (
    echo OmniFetch 本地助手已退出。
)
echo.
pause
exit /b %EXIT_CODE%
