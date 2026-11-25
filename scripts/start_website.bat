@echo off
rem Starts the DormCare website on top of the publish-subscribe broker and opens it at http://127.0.0.1:8000
rem It uses the broker already running on port 5555 (e.g. the dashboard's or the command-line demo's),
rem or starts one if none is running. Notifications in the app then travel through the broker.
title DormCare website
cd /d "%~dp0.."
call "%~dp0find_python.bat" || (pause & exit /b 1)
%PY% -m pubsub.webapp %*
echo.
echo   Website stopped. Press any key to close this window.
pause >nul
