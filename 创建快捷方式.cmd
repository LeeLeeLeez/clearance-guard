@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem ================================================================
rem  在「开始菜单」和「桌面」各放一个 Clearance Guard 快捷方式
rem
rem  * 指向 run.cmd（自动起服务 + 用 Edge 应用模式开独立窗口）
rem  * 图标用 assets\app_icon.ico
rem  * 窗口样式设为最小化，减少 cmd 那一瞬间的闪现
rem
rem  删掉快捷方式：从开始菜单/桌面直接删除即可，不影响程序本体。
rem ================================================================

set "TARGET=%~dp0run.cmd"
set "WORKDIR=%~dp0"
set "ICON=%~dp0assets\app_icon.ico"

if not exist "%TARGET%" (
    echo [错误] 找不到 run.cmd
    pause
    exit /b 1
)
if not exist "%ICON%" (
    echo [提示] 找不到 assets\app_icon.ico，快捷方式将使用默认图标。
    set "ICON=%TARGET%"
)

echo 正在创建快捷方式…
echo   目标：%TARGET%
echo   图标：%ICON%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$sh=New-Object -ComObject WScript.Shell; $dirs=@([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop')); foreach($d in $dirs){ if(-not (Test-Path $d)){ continue }; $lnk=Join-Path $d 'Clearance Guard.lnk'; $s=$sh.CreateShortcut($lnk); $s.TargetPath='%TARGET%'; $s.WorkingDirectory='%WORKDIR%'; $s.IconLocation='%ICON%'; $s.Description='Clearance Guard - Altium Designer 避距/爬电审核'; $s.WindowStyle=7; $s.Save(); Write-Host ('  已创建: ' + $lnk) }"

if errorlevel 1 (
    echo.
    echo [错误] 创建失败。可手动操作：
    echo        右键 run.cmd ^> 发送到 ^> 桌面快捷方式，
    echo        再把它拖到「开始」按钮上固定。
    pause
    exit /b 1
)

echo.
echo 完成。可以在「开始」菜单里搜 Clearance Guard 打开；
echo 右键该快捷方式还能「固定到任务栏」或「固定到开始屏幕」。
echo.
pause
