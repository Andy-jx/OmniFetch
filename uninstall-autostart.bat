@echo off
setlocal EnableExtensions
chcp 65001 >nul

title OmniFetch - 取消自动启动

set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\OmniFetch Helper.lnk"

if exist "%LINK%" (
    del /f /q "%LINK%"
)

if exist "%LINK%" (
    echo [错误] 删除自动启动快捷方式失败。
    echo.
    pause
    exit /b 1
)

echo.
echo [完成] 已取消 OmniFetch 流媒体助手的 Windows 自动启动。
echo [说明] 不会删除程序、扩展或已经下载的视频。
echo.
pause
exit /b 0
