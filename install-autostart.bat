@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

title OmniFetch - 安装自动启动

if not exist "OmniFetchHelper.exe" (
    echo [错误] 当前目录没有 OmniFetchHelper.exe。
    echo 请在 GitHub Actions 下载 OmniFetch-Windows 便携包后再运行本脚本。
    echo.
    pause
    exit /b 1
)

if not exist "start-helper-hidden.vbs" (
    echo [错误] 当前目录没有 start-helper-hidden.vbs。
    echo.
    pause
    exit /b 1
)

set "OMNIFETCH_DIR=%~dp0"
set "OMNIFETCH_EXE=%~dp0OmniFetchHelper.exe"
set "OMNIFETCH_VBS=%~dp0start-helper-hidden.vbs"
set "OMNIFETCH_LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\OmniFetch Helper.lnk"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut($env:OMNIFETCH_LINK); $s.TargetPath=$env:SystemRoot+'\System32\wscript.exe'; $s.Arguments='\"'+$env:OMNIFETCH_VBS+'\"'; $s.WorkingDirectory=$env:OMNIFETCH_DIR; $s.IconLocation=$env:OMNIFETCH_EXE+',0'; $s.Save()"

if errorlevel 1 (
    echo [错误] 创建开机启动快捷方式失败。
    echo.
    pause
    exit /b 1
)

start "" wscript.exe "%~dp0start-helper-hidden.vbs"

echo.
echo [完成] OmniFetch 流媒体助手已设置为 Windows 登录后自动后台启动。
echo [提示] 请不要随意移动整个 OmniFetch 文件夹，否则启动快捷方式路径会失效。
echo [取消] 如需关闭自动启动，双击 uninstall-autostart.bat。
echo.
pause
exit /b 0
