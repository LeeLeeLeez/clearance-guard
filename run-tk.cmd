@echo off
chcp 936 >nul
setlocal
title Clearance Guard (PySide6)
cd /d "%~dp0"

rem ================================================================
rem  旧版界面启动器（PySide6）—— 保留作回退
rem  新版界面请双击 run.cmd
rem ================================================================

set "APP=%~dp0app.py"
if not exist "%APP%" (
    echo [错误] 未找到 app.py，请把 run-tk.cmd 放在项目根目录。
    pause
    exit /b 1
)

set "PYEXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not defined PYEXE if exist "%~dp0venv\Scripts\python.exe" set "PYEXE=%~dp0venv\Scripts\python.exe"
if not defined PYEXE for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined PYEXE set "PYEXE=%%P"
if not defined PYEXE for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYEXE set "PYEXE=%%P"

if not defined PYEXE (
    echo.
    echo [错误] 没有找到 Python 3.10+。
    echo.
    pause
    exit /b 1
)

"%PYEXE%" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo [提示] 当前 Python 缺少 PySide6，正在安装依赖...
    "%PYEXE%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo [错误] 依赖安装失败，请手动执行：
        echo        "%PYEXE%" -m pip install PySide6
        echo.
        pause
        exit /b 1
    )
)

"%PYEXE%" -c "import clearance_check.ui.main_window" >nul 2>nul
if errorlevel 1 (
    echo [错误] 程序导入失败，完整错误如下：
    echo.
    "%PYEXE%" -c "import clearance_check.ui.main_window"
    echo.
    pause
    exit /b 1
)

set "PYW=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PYEXE%"

echo 正在启动 Clearance Guard（旧版 PySide6 界面）...
if /i "%PYW%"=="%PYEXE%" (
    "%PYEXE%" "%APP%" %*
    set "CODE=%ERRORLEVEL%"
) else (
    start "" "%PYW%" "%APP%" %*
    set "CODE=0"
)

if not "%CODE%"=="0" (
    echo.
    echo 程序异常退出，错误码 %CODE%。
    pause
)
endlocal
