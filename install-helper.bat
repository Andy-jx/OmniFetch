@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo OmniFetch - 本地下载助手安装程序
echo ============================================================
echo.

set "PYTHON_CMD="
where py >nul 2>nul
if %errorlevel%==0 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    where python >nul 2>nul
    if %errorlevel%==0 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo [错误] 没有检测到 Python 3。
    echo 请先安装 Python 3.10 或更高版本，并勾选 Add Python to PATH。
    echo.
    pause
    exit /b 1
)

echo [1/4] 检测 Python...
%PYTHON_CMD% --version
if errorlevel 1 goto :failed

echo.
echo [2/4] 创建独立运行环境...
if not exist ".venv\Scripts\python.exe" (
    %PYTHON_CMD% -m venv ".venv"
    if errorlevel 1 goto :failed
) else (
    echo 已存在 .venv，跳过创建。
)

echo.
echo [3/4] 安装 / 更新依赖...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r "helper\requirements.txt"
if errorlevel 1 goto :failed

echo.
echo [4/4] 检测 FFmpeg...
where ffmpeg >nul 2>nul
if %errorlevel%==0 (
    echo FFmpeg：已检测到。
) else if exist "helper\tools\ffmpeg.exe" (
    echo FFmpeg：已检测到 helper\tools\ffmpeg.exe。
) else (
    echo FFmpeg：未检测到。
    echo 提示：没有 FFmpeg 也可以使用，但部分网站只能下载单文件格式，
    echo       最高画质的视频与音频合并可能不可用。
)

echo.
echo ============================================================
echo 安装完成。
echo 下一步：双击 run-helper.bat 启动本地助手。
echo 默认保存目录：%%USERPROFILE%%\Downloads\OmniFetch
echo ============================================================
echo.
pause
exit /b 0

:failed
echo.
echo [错误] 安装过程中出现问题，请保留当前窗口中的错误信息。
echo.
pause
exit /b 1
