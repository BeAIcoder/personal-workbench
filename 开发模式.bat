@echo off
setlocal
chcp 936 >nul
title 个人工作台 - 开发模式
cd /d "%~dp0"

if not exist "backend\venv\Scripts\python.exe" (
    echo 请先运行一次 启动工作台.bat 完成环境初始化。
    pause
    exit /b 1
)

echo 正在启动后端（热重载，8000 端口）和前端（Vite 热更新，5173 端口）...
start "个人工作台-后端(开发)" /D "%~dp0backend" "%~dp0backend\venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
start "个人工作台-前端(开发)" /D "%~dp0frontend" cmd /k npm run dev
timeout /t 4 /nobreak >nul
start http://localhost:5173

echo.
echo 开发模式已启动： http://localhost:5173 （前端热更新）
echo 接口直连地址：   http://127.0.0.1:8000/docs
echo 关闭两个控制台窗口即可停止。
pause
