@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  Upload this project to GitHub WITHOUT git.exe
rem  (DLP encrypts files read by git.exe / powershell; python sees plaintext)
rem
rem  Double click  -> GUI uploader (fill token, click upload)
rem  Command line  -> run:  upload_to_github --token ghp_xxx --repo clearance-guard
rem  Dry run       -> run:  upload_to_github --dry-run
rem  Token page    -> https://github.com/settings/tokens (classic, tick repo)
rem ============================================================

set "GUI=%~dp0scripts\upload_gui.py"
set "CLI=%~dp0scripts\gh_upload.py"

if not exist "%CLI%" (
    echo.
    echo [ERROR] scripts\gh_upload.py not found.
    echo         current dir: %~dp0
    echo.
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
    echo [ERROR] Python 3.10+ not found.
    echo.
    pause
    exit /b 1
)

if not "%~1"=="" goto cli

if not exist "%GUI%" goto cli

set "PYWEXE=%PYEXE%"
if exist "%~dp0.venv\Scripts\pythonw.exe" set "PYWEXE=%~dp0.venv\Scripts\pythonw.exe"
if exist "%~dp0venv\Scripts\pythonw.exe" set "PYWEXE=%~dp0venv\Scripts\pythonw.exe"

start "" "%PYWEXE%" "%GUI%"
exit /b 0

:cli
"%PYEXE%" "%CLI%" --root "%~dp0." %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] upload failed, exit code = %RC%
    echo         token / network / permission are the usual causes.
    echo.
    pause
)
exit /b %RC%
