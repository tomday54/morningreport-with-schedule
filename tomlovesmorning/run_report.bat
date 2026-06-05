@echo off
REM Morning Energy Report launcher for Windows Task Scheduler.
REM Runs the Python script using the user-scope Python install.
REM ANTHROPIC_API_KEY must exist as a user environment variable.

set PYTHONIOENCODING=utf-8
set PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe

cd /d "%~dp0"
echo [%DATE% %TIME%] launcher start (pyexe=%PYEXE%) >> "%~dp0launcher.log"
"%PYEXE%" "%~dp0morning_report.py" %* >> "%~dp0launcher.log" 2>&1
echo [%DATE% %TIME%] launcher exit code %ERRORLEVEL% >> "%~dp0launcher.log"
