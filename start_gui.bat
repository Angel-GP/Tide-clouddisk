@echo off
chcp 65001 >nul
title PAN Manager
cd /d "%~dp0"

rem 优先使用打包好的 exe (免 Python 环境)
if exist "%~dp0*.exe" (
    for %%F in ("%~dp0*.exe") do (
        start "" "%%F"
        exit /b 0
    )
)

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

%PYCMD% gui.py
pause
