@echo off
chcp 65001 >nul
title 深城修改器

REM ============================================================
REM  启动器（可移植版）
REM  Python 解释器查找顺序：
REM    1) 本目录下的 python_path.txt（里面写一行 pythonw.exe 的完整路径）
REM    2) 环境变量 PYTHONW / PYTHON
REM    3) PATH 里的 pythonw / python
REM ============================================================

set "APP=%~dp0src\simple.py"
set "PYW="

if exist "%~dp0python_path.txt" (
    set /p PYW=<"%~dp0python_path.txt"
)
if not defined PYW if defined PYTHONW set "PYW=%PYTHONW%"
if not defined PYW if defined PYTHON set "PYW=%PYTHON%"
if not defined PYW (
    where pythonw >nul 2>nul && set "PYW=pythonw"
)
if not defined PYW (
    where python >nul 2>nul && set "PYW=python"
)

if not defined PYW goto err_py
if not exist "%APP%" goto err_app

echo 使用解释器: %PYW%
start "" "%PYW%" "%APP%"
exit /b 0

:err_py
echo [错误] 找不到 Python 解释器。
echo   请在 python_path.txt 里写一行 pythonw.exe 的完整路径，
echo   或把 python / pythonw 加进 PATH。
echo   依赖：pip install -r requirements.txt
pause
exit /b 1

:err_app
echo [错误] 找不到 %APP%
echo   请确认 src\simple.py 存在（仓库结构：src/ 放源码）。
pause
exit /b 1
