@echo off
setlocal
chcp 936 >nul
title 个人工作台 - 打包分发包
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo 错误：未找到 python 命令，请先安装 Python 3.12+ 并加入 PATH。
    pause
    exit /b 1
)

echo ================================================
echo   个人工作台 分发包制作
echo   （导出脱敏树 - 重建前端 - 敏感串扫描 - 打包 - 冒烟）
echo ================================================
echo.

python dev\package_release.py %*
if errorlevel 1 (
    echo.
    echo 打包失败，请阅读上方输出排查。
    pause
    exit /b 1
)

echo.
echo 打包完成，产物在 backups\releases\ 目录。
pause
