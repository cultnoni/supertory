@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE="

rem Find Python without a machine-specific path: py launcher, then PATH, then common installs.
for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Python\bin\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Python\bin\python.exe"

if not defined PYTHON_EXE (
  echo Python was not found. Install Python 3 from python.org and run this file again.
  pause
  exit /b 1
)

rem DB location follows SUPERTORY_DB_MODE in app.py (default: shared AppData).
rem Do not set SUPERTORY_DB_MODE here. Use start_supertory_isolated.bat for repo data.

rem Optional argument: path to a .stg project file (Scrivener-style open).
"%PYTHON_EXE%" app.py %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo SuperTory has stopped. Press any key to close this window.
pause >nul
exit /b %EXIT_CODE%
