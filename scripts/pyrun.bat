@echo off
rem Runs Python with the interpreter find_python.bat locates, from the project folder.
rem Use it when "python" is not on your PATH, e.g.:
rem   scripts\pyrun.bat -m pubsub.publisher --id manager-office-2 --topic casa-jaro.billing --title "Rent due"
call "%~dp0find_python.bat" || exit /b 1
pushd "%~dp0.."
%PY% %*
set "CODE=%errorlevel%"
popd
exit /b %CODE%
