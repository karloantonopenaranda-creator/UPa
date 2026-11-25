@echo off
rem Builds DormCare_Submission.zip, or DormCare_Submission_DRAFT.zip while names or page numbers are missing.
rem See make_submission.py. (The "_DRAFT" in the name is the reminder; the file layout is the same.)
cd /d "%~dp0.."
call "%~dp0find_python.bat" || exit /b 1
%PY% scripts\make_submission.py
