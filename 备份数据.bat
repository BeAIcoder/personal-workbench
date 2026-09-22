@echo off
setlocal
chcp 936 >nul
title 个人工作台 - 备份数据
cd /d "%~dp0"

set "PY=backend\venv\Scripts\python.exe"
if not exist "%PY%" (
    echo 未找到后端虚拟环境：%PY%
    echo 请先在 backend 目录创建 venv 并安装依赖，或手动运行：
    echo     python backend\scripts\backup_db.py
    pause
    exit /b 1
)

"%PY%" backend\scripts\backup_db.py
if errorlevel 1 (
    echo 备份失败，请检查上方提示。
)
pause
