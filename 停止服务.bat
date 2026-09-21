@echo off
setlocal
chcp 936 >nul
title 个人工作台 - 停止服务

echo 正在停止个人工作台后端服务（仅结束监听 8000 端口的进程）...
set found=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /i ":8000 .*LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
    set found=1
)
if "%found%"=="1" (
    echo 已停止。
) else (
    echo 没有发现正在运行的服务。
)
pause
