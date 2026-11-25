@echo off
rem Runs the broker. If it stops or is crashed, press any key in this window to start it again
rem (that is how the "broker crash and recovery" step of the demo is done).
rem Extra options are passed through, e.g.  run_broker.bat --fault-skip-puback 1
title BROKER
cd /d "%~dp0.."
call "%~dp0find_python.bat" || (pause & exit /b 1)
:loop
%PY% -m pubsub.broker %*
echo.
echo   Broker stopped. Press any key to start it again (queues are recovered from data\broker.db),
echo   or close this window.
pause >nul
goto loop
