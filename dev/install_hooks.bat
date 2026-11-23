@echo off
setlocal
set "ROOT=%~dp0.."
if not exist "%ROOT%\.git\hooks" mkdir "%ROOT%\.git\hooks"
copy /Y "%ROOT%\dev\hooks\pre-push" "%ROOT%\.git\hooks\pre-push" >nul
echo Installed .git/hooks/pre-push
