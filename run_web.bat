@echo off
chcp 65001 >nul
title AI Broker Web
cd /d "%~dp0"

echo.
echo ========================================
echo   AI Broker - local web server
echo   Folder: %CD%
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 goto try_py

python --version
echo Installing aibroker[web]...
python -m pip install -e ".[web]"
if errorlevel 1 goto pip_fail

echo.
echo Auto-picking a free port (8765+) so old servers do not block you.
echo Browser opens automatically. Stop server: Ctrl+C
echo.
python -m aibroker.cli web --auto-port %*
goto end

:try_py
where py >nul 2>&1
if errorlevel 1 goto no_python

echo Using Python launcher: py -3
py -3 --version
echo Installing aibroker[web]...
py -3 -m pip install -e ".[web]"
if errorlevel 1 goto pip_fail

echo.
echo Auto-picking a free port. Browser opens from the server.
echo.
py -3 -m aibroker.cli web --auto-port %*
goto end

:no_python
echo ERROR: Python not found in PATH.
echo Install from https://www.python.org/downloads/
echo Enable "Add python.exe to PATH" during setup.
goto end_pause

:pip_fail
echo ERROR: pip install failed. Run this folder in a terminal and read the message above.
goto end_pause

:end
echo.
echo Server stopped. Exit code: %errorlevel%
goto end_pause

:end_pause
pause
