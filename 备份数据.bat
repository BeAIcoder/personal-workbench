@echo off
setlocal
chcp 936 >nul
title 个人工作台 - 备份数据
cd /d "%~dp0"

if not exist "backend\data\workbench.db" (
    echo 未找到数据库文件，请先启动一次服务让它生成数据。
    pause
    exit /b 1
)

set "backup_dir=backups"
if not exist "%backup_dir%" mkdir "%backup_dir%"

set "stamp=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "stamp=%stamp: =0%"
set "stamp=%stamp:/=%"
set "stamp=%stamp::=%"

copy /y "backend\data\workbench.db" "%backup_dir%\workbench_%stamp%.db" >nul
if errorlevel 1 (
    echo 备份失败，请检查磁盘权限。
) else (
    echo 备份成功：%backup_dir%\workbench_%stamp%.db
)
pause
