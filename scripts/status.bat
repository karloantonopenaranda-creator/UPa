@echo off
rem Prints every subscriber's queue on the broker: online/offline, messages waiting, topics.
cd /d "%~dp0.."
call "%~dp0find_python.bat" || exit /b 1
%PY% -m pubsub.publisher --id status-check --status %*
