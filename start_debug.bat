@echo off
chcp 65001 >nul
title Tide cloud - Debug Mode
cd /d "%~dp0"

rem 优先使用打包好的 exe 以调试模式启动
if exist "%~dp0*.exe" (
    for %%F in ("%~dp0*.exe") do (
        start "Tide cloud Debug" "%%F" --debug
        exit /b 0
    )
)

set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (
    where python >nul 2>nul && set "PYCMD=python"
)
if not defined PYCMD (
    echo [ERROR] Python 3 not found.
    pause
    exit /b 1
)

%PYCMD% gui.py --debug
pause
