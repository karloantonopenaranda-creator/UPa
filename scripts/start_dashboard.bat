@echo off
rem Starts the web dashboard ("mailroom") and opens it in your browser at http://127.0.0.1:8080
rem The dashboard starts the broker and subscribers itself: press "Start demo" on the page.
rem Close the command-line demo windows first (they use the same broker port, 5555).
title DormCare dashboard
cd /d "%~dp0.."
call "%~dp0find_python.bat" || (pause & exit /b 1)
%PY% -m pubsub.dashboard %*
echo.
echo   Dashboard stopped. Press any key to close this window.
pause >nul
