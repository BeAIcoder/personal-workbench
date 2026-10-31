@echo off
rem 服务端口：改端口只改这一行，需与 backend\.env 的 PORT 一致
set "PORT=8000"
setlocal
chcp 936 >nul
title 个人工作台 - 停止服务

set "pid="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /i ":%PORT%.*LISTENING"') do set "pid=%%a"

if not defined pid (
    echo 没有发现正在运行的服务。
    pause
    exit /b 0
)

echo 监听 %PORT% 端口的进程 PID：%pid%
set "cmdline="
for /f "delims=" %%c in ('powershell -noprofile -command "(Get-CimInstance Win32_Process -Filter 'ProcessId=%pid%').CommandLine"') do set "cmdline=%%c"
echo 命令行：%cmdline%

echo %cmdline% | findstr /i "uvicorn python" >nul
if errorlevel 1 (
    echo 该进程不是 uvicorn/python，%PORT% 端口可能被其他程序占用，已取消操作。
    pause
    exit /b 1
)

choice /m "确认结束该进程吗"
if errorlevel 2 (
    echo 已取消，未做任何操作。
    pause
    exit /b 0
)

taskkill /PID %pid% /F >nul 2>&1
if errorlevel 1 (
    echo 停止失败，请检查权限。
) else (
    echo 已停止。
)
pause
