@echo off
chcp 65001 >nul
title PAN File Server
cd /d "%~dp0"

:loop
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (
    where python >nul 2>nul && set "PYCMD=python"
)
if not defined PYCMD (
    echo.
    echo [ERROR] Python 3 not found.
    echo         Please install it from https://www.python.org/downloads/
    echo         and check "Add Python to PATH" during setup.
    echo.
    pause
    exit /b 1
)

%PYCMD% server.py
if errorlevel 3 (
    echo.
    echo Server exited. Restarting...
    timeout /t 2 >nul
    goto loop
)
echo.
echo Server stopped.
pause
