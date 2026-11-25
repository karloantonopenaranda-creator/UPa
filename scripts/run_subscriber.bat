@echo off
rem Usage:  run_subscriber.bat <client-id> [topic1,topic2,...] [extra options]
rem Runs a subscriber. If it stops or is crashed, press any key to start it again with the SAME id:
rem it reconnects, and the broker sends everything it missed (catch-up).
rem The restart deliberately omits --topics, to show the broker remembered the subscriptions.
rem Extra options (e.g. --crash-after 1) apply to the FIRST run only, so a crash demo restarts normally.
rem
rem The topic list may be quoted or not. Batch splits unquoted arguments at commas (and PowerShell removes
rem the quotes), so every argument before the first one starting with "-" is joined back into the topic list.
title SUB %~1
cd /d "%~dp0.."
call "%~dp0find_python.bat" || (pause & exit /b 1)
set "ID=%~1"
set "TOPICS="
set "EXTRA="

:next_arg
shift /1
if [%1]==[] goto loop
set "ARG=%~1"
if not defined ARG goto next_arg
if defined EXTRA goto add_extra
if "%ARG:~0,1%"=="-" goto add_extra
if defined TOPICS (set "TOPICS=%TOPICS%,%ARG%") else (set "TOPICS=%ARG%")
goto next_arg
:add_extra
set "EXTRA=%EXTRA% %1"
goto next_arg

:loop
if defined TOPICS (
    %PY% -m pubsub.subscriber --id %ID% --topics %TOPICS% %EXTRA%
) else (
    %PY% -m pubsub.subscriber --id %ID% %EXTRA%
)
set "TOPICS="
set "EXTRA="
echo.
echo   Subscriber %ID% stopped. Press any key to start it again with the same id (no --topics needed:
echo   subscriptions are durable), or close this window.
pause >nul
goto loop
