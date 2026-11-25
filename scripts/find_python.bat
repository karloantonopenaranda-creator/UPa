@echo off
rem Finds a working Python 3 and stores the command in the PY variable.
rem The Windows Store "python" alias does not count: it only opens the Store.
rem To force a specific interpreter:  set DORMCARE_PYTHON=C:\path\to\python.exe
set "PY="
if defined DORMCARE_PYTHON (
    set "PY="%DORMCARE_PYTHON%""
    goto :found
)
py -3 -c "import sqlite3" >nul 2>&1 && (set "PY=py -3" & goto :found)
python -c "import sqlite3" >nul 2>&1 && (set "PY=python" & goto :found)
for %%P in ("%USERPROFILE%\anaconda3\python.exe" "%USERPROFILE%\miniconda3\python.exe" "%LOCALAPPDATA%\anaconda3\python.exe" "%PROGRAMDATA%\anaconda3\python.exe") do (
    if exist %%P (
        %%P -c "import sqlite3" >nul 2>&1 && (set "PY=%%P" & goto :found)
    )
)
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" (
        set "PY="%%D\python.exe""
        goto :found
    )
)
echo Could not find Python 3.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
echo or set DORMCARE_PYTHON to the full path of python.exe.
exit /b 1
:found
exit /b 0
