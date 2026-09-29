@echo off
chcp 65001 >nul
title AIP Dev MCP Server

echo ============================================
echo  AIP Dev MCP Server - 启动脚本
echo ============================================
echo.

set "PROJ=%~dp0"
if not exist "%PROJ%server.py" (
    echo [错误] 项目目录下找不到 server.py: %PROJ%
    pause
    exit /b 1
)

REM 解释器必须用本仓库自带的 venv（内含 mcp 2.x），不要用 miniconda / 其它 venv
set "PY=%PROJ%venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [错误] 未找到 venv 解释器: %PY%
    echo        请先执行: python -m venv venv
    echo                   venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo [1/2] 环境检查通过
echo   Python: %PY%
echo   目录:   %PROJ%
echo   提示: 若上一次的窗口还没关，请先 Ctrl+C 结束它，避免两个进程抢 stdio
echo.
echo [2/2] 启动 MCP 服务...
echo ============================================
echo  服务启动中... 此窗口保持打开状态
echo  按 Ctrl+C 停止服务
echo ============================================
echo.

REM -X utf8 必须带：config.yaml 是 UTF-8，Windows 中文系统默认 GBK，不加会 UnicodeDecodeError
"%PY%" -X utf8 "%PROJ%server.py"

echo.
echo ============================================
echo  服务已退出 (exit code: %errorlevel%)
echo ============================================
pause
