@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
  if errorlevel 1 goto :fail
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :fail
.venv\Scripts\python.exe launch.py
if errorlevel 1 goto :fail
exit /b 0
:fail
echo Setup or launch failed. Python 3.11 or newer is required. Read the error above.
pause
exit /b 1
