@echo off
chcp 65001 >nul
title AIP Dev MCP Server

echo ============================================
echo  AIP Dev MCP Server - 启动脚本
echo ============================================
echo.

REM 检查 WSL 是否可用
wsl echo "WSL OK" >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] WSL 不可用，请确认 WSL 已安装并启动
    pause
    exit /b 1
)

REM 检查项目目录是否存在
wsl bash -c "test -d /mnt/g/histoneGitFiles/aip-dev-mcp && echo OK || echo MISSING" | findstr "MISSING" >nul
if %errorlevel% equ 0 (
    echo [错误] 项目目录不存在: G:\histoneGitFiles\aip-dev-mcp
    pause
    exit /b 1
)

echo [1/2] 终止已有进程...
wsl bash -c "pkill -f 'aip-dev-mcp/server.py' 2>/dev/null && echo '  已终止旧进程' || echo '  无旧进程在运行'"

echo.
echo [2/2] 启动 MCP 服务...
echo   Python: /home/wejie110/.hermes.wsl.bak/hermes-agent/venv/bin/python3
echo   目录:   /mnt/g/histoneGitFiles/aip-dev-mcp
echo   文件:   server.py
echo.
echo ============================================
echo  服务启动中... 此窗口保持打开状态
echo  按 Ctrl+C 停止服务
echo ============================================
echo.

wsl bash -c "cd /mnt/g/histoneGitFiles/aip-dev-mcp && /home/wejie110/.hermes.wsl.bak/hermes-agent/venv/bin/python3 server.py 2>&1"

echo.
echo ============================================
echo  服务已退出 (exit code: %errorlevel%)
echo ============================================
pause
