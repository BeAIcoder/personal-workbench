@echo off
setlocal
chcp 936 >nul
title 个人工作台 - 一键启动
cd /d "%~dp0"

echo ================================================
echo              个人工作台 一键启动
echo ================================================
echo.

if not exist "backend\venv\Scripts\python.exe" goto setup_backend
goto check_frontend

:setup_backend
echo [1/4] 首次运行：正在创建 Python 虚拟环境...
where python >nul 2>nul
if errorlevel 1 (
    echo 错误：未找到 python 命令，请先安装 Python 3.12+ 并勾选"加入 PATH"。
    pause
    exit /b 1
)
python -m venv backend\venv
if errorlevel 1 (
    echo 错误：创建虚拟环境失败。
    pause
    exit /b 1
)
echo       正在安装后端依赖（首次约 1-3 分钟）...
backend\venv\Scripts\python.exe -m pip install -r backend\requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo       默认源安装失败，改用清华镜像重试...
    backend\venv\Scripts\python.exe -m pip install -r backend\requirements.txt --quiet --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo 错误：后端依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
)
echo       后端依赖安装完成。

:check_frontend
if exist "frontend\dist\index.html" goto start_server
echo [2/4] 首次运行：正在安装前端依赖...
where npm >nul 2>nul
if errorlevel 1 (
    echo 错误：未找到 npm 命令，请先安装 Node.js 22+。
    pause
    exit /b 1
)
pushd frontend
call npm install --no-audit --no-fund
if errorlevel 1 (
    echo       默认源安装失败，改用国内镜像重试...
    call npm install --no-audit --no-fund --registry=https://registry.npmmirror.com
    if errorlevel 1 (
        echo 错误：npm 依赖安装失败，请检查网络后重试。
        popd
        pause
        exit /b 1
    )
)
echo [3/4] 正在构建前端页面...
call npm run build
if errorlevel 1 (
    echo 错误：前端构建失败。
    popd
    pause
    exit /b 1
)
popd
echo       前端构建完成。

:start_server
echo [4/4] 正在启动服务（会弹出一个后端控制台窗口，请勿关闭它）...
start "个人工作台-后端服务" /D "%~dp0backend" "%~dp0backend\venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
timeout /t 3 /nobreak >nul
start http://127.0.0.1:8000

echo.
echo ==================================================
echo  服务已启动： http://127.0.0.1:8000
echo  接口文档：   http://127.0.0.1:8000/docs
echo  停止方法：   关闭"个人工作台-后端服务"窗口，
echo               或双击 停止服务.bat
echo  本窗口可以关闭。
echo ==================================================
pause
