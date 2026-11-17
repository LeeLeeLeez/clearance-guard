@echo off
chcp 65001 >nul
setlocal
title Clearance Guard
cd /d "%~dp0"

rem ================================================================
rem  Clearance Guard 启动器（oink-ui / Web 版）
rem
rem  行为：
rem    1) 本地服务（127.0.0.1:8770）没在跑 → 用 pythonw 后台拉起（无黑窗）
rem    2) 用 Edge 的「应用模式」打开 —— 无地址栏、无标签页，任务栏独立图标
rem    3) 没有 Edge 就退回默认浏览器
rem
rem  想加开始菜单/桌面快捷方式：双击「创建快捷方式.cmd」
rem  旧版 PySide6 界面：双击 run-tk.cmd
rem ================================================================

set "PORT=8770"
set "APPURL=http://127.0.0.1:%PORT%/"
set "EDGE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=C:\Program Files\Microsoft\Edge\Application\msedge.exe"

if not exist "%~dp0server.py" (
    echo [错误] 未找到 server.py，请把 run.cmd 放在项目根目录。
    pause
    exit /b 1
)
if not exist "%~dp0web\dist\index.html" (
    echo.
    echo [错误] 前端还没构建。
    echo        请在项目根执行：
    echo            cd web
    echo            npm install
    echo            npm run build
    echo.
    pause
    exit /b 1
)

rem ---------------------------------------------------------------- 找 Python
set "PYEXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not defined PYEXE if exist "%~dp0venv\Scripts\python.exe" set "PYEXE=%~dp0venv\Scripts\python.exe"
if not defined PYEXE for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined PYEXE set "PYEXE=%%P"
if not defined PYEXE for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYEXE set "PYEXE=%%P"

if not defined PYEXE (
    echo.
    echo [错误] 没有找到 Python 3.10+。
    echo        请安装 Python，并在安装时勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)

set "PYW=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PYEXE%"

rem ------------------------------------------------- 服务是否已在跑？（socket 探测，无窗口）
"%PYEXE%" -c "import socket,sys;s=socket.socket();s.settimeout(0.5);sys.exit(0 if s.connect_ex(('127.0.0.1',%PORT%))==0 else 1)" >nul 2>&1
if not errorlevel 1 goto open

rem ------------------------------------------------- 起服务（后台，不弹窗）
"%PYEXE%" -c "import server" >nul 2>&1
if errorlevel 1 (
    echo [错误] 后端导入失败，完整错误如下：
    echo.
    "%PYEXE%" -c "import server"
    echo.
    pause
    exit /b 1
)

start "" "%PYW%" "%~dp0server.py" --no-browser --port %PORT%

rem 等端口就绪，最多 30 秒
for /l %%i in (1,1,30) do (
    "%PYEXE%" -c "import socket,sys;s=socket.socket();s.settimeout(0.4);sys.exit(0 if s.connect_ex(('127.0.0.1',%PORT%))==0 else 1)" >nul 2>&1
    if not errorlevel 1 goto open
    ping -n 2 127.0.0.1 >nul
)

echo [提示] 服务启动较慢，已跳过等待，直接打开页面。

:open
if exist "%EDGE%" (
    start "" "%EDGE%" --app=%APPURL%
) else (
    start "" %APPURL%
)
exit /b 0
