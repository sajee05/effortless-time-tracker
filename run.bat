@echo off
REM This command starts the Python script using the windowless Python executable (pythonw.exe),
REM which prevents the console window from opening. The script will run as a background process.

start "TimeTracker" pythonw.exe "C:\Users\FO\Downloads\time-tracker\tracker.py"

exit
