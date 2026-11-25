@echo off
rem Runs the whole automated test suite. Takes 1.5 to 2.5 minutes; a few minimised windows open briefly.
cd /d "%~dp0.."
call "%~dp0find_python.bat" || (pause & exit /b 1)
%PY% -m unittest discover -s tests -t . -v
