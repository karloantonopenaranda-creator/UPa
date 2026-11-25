@echo off
rem Usage:  run_publisher.bat <client-id> [extra options]
rem Runs an interactive publisher (type "help" at its prompt).
title PUB %~1
cd /d "%~dp0.."
call "%~dp0find_python.bat" || (pause & exit /b 1)
:loop
%PY% -m pubsub.publisher --id %~1 %2 %3 %4 %5 %6 %7 %8 %9
echo.
echo   Publisher %~1 stopped. Press any key to start it again, or close this window.
pause >nul
goto loop
