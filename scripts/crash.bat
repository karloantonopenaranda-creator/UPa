@echo off
rem Usage:  crash.bat tenant-juan      hard-kill that subscriber/publisher (simulated crash)
rem         crash.bat broker           hard-kill the broker
if "%~1"=="" (
    echo Usage: crash.bat ^<client-id^>   or   crash.bat broker
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0crash.ps1" -Target %~1
