@echo off
rem Opens the whole DormCare Live Alerts demo in 6 windows:
rem   1 broker, 3 subscribers (2 tenants + caretaker), 2 publishers (manager office + maintenance desk).
rem Run reset_data.bat first for a clean demo.
cd /d "%~dp0.."
call "%~dp0find_python.bat" || (pause & exit /b 1)

set /a TRIES=0
:start_broker
set /a TRIES+=1
rem The windows are opened as   cmd /k ".\scripts\name.bat args"   on purpose. cmd removes the first and last
rem quote of the text after /k, so that text must hold no other quotes. The path is relative (we are in the
rem project folder), so it needs none, even if the project folder has spaces or brackets in its name.
start "BROKER" cmd /k ".\scripts\run_broker.bat"
rem Wait up to about 8 s for the broker to listen. If its window did not come up (this can happen when the
rem terminal app starts slowly the first time), open it again. find has a full path, because Git for Windows
rem also has a program called find.
for /l %%i in (1,1,8) do (
    netstat -an | "%SystemRoot%\System32\find.exe" "127.0.0.1:5555" | "%SystemRoot%\System32\find.exe" "LISTENING" >nul && goto :broker_up
    ping -n 2 127.0.0.1 >nul
)
if %TRIES% lss 3 goto start_broker
echo The broker did not start. Double-click scripts\run_broker.bat to start it by hand.
:broker_up

start "SUB tenant-juan" cmd /k ".\scripts\run_subscriber.bat tenant-juan casa-jaro.announcements,casa-jaro.maintenance,casa-jaro.billing"
start "SUB tenant-jerome" cmd /k ".\scripts\run_subscriber.bat tenant-jerome molo-hub.announcements,molo-hub.billing"
start "SUB caretaker" cmd /k ".\scripts\run_subscriber.bat caretaker casa-jaro.maintenance,molo-hub.maintenance"
start "PUB manager-office" cmd /k ".\scripts\run_publisher.bat manager-office"
start "PUB maintenance-desk" cmd /k ".\scripts\run_publisher.bat maintenance-desk"

echo Demo windows opened. Useful commands from this window:
echo   scripts\status.bat               show every subscriber's queue on the broker
echo   scripts\crash.bat tenant-juan    hard-kill a subscriber (simulated crash)
echo   scripts\crash.bat broker         hard-kill the broker (simulated crash)
