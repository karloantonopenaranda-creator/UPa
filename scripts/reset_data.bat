@echo off
rem Deletes all broker queues, durable subscriptions and subscriber logs, for a clean demo.
rem Close the broker AND subscriber windows first. The database is locked while the broker runs, and a
rem subscriber restarted without --topics would have no subscriptions left in the new database.
cd /d "%~dp0.."
if not exist data (
    echo Nothing to delete: there is no data folder yet.
    exit /b 0
)
rmdir /s /q data
if exist data (
    echo Could not delete everything. Close the broker and subscriber windows, then run this again.
    exit /b 1
)
echo Demo data deleted. The next broker start begins with empty queues.
