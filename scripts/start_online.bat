@echo off
rem Shares the DormCare website on your fixed public ngrok link (https://<name>.ngrok-free.app) that any phone or
rem laptop can open, on any network, with nothing to install. It uses the website already running on port 8000 or
rem starts it (and the broker). Anyone with the link can use the site until you press Ctrl+C in this window.
rem One-time setup:  winget install --id 9MVS1J51GMK6 --source msstore   (ngrok from the Microsoft Store)
rem                  then a free account at https://dashboard.ngrok.com/signup and:  ngrok config add-authtoken <token>
title DormCare online link
cd /d "%~dp0.."
call "%~dp0find_python.bat" || (pause & exit /b 1)
%PY% scripts\start_online.py %*
echo.
echo   Stopped. Press any key to close this window.
pause >nul
