@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=C:\Users\cultn\AppData\Local\Python\bin\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Python was not found. Please tell Codex.
  pause
  exit /b 1
)

rem Optional argument: path to a .stg project file (Scrivener-style open).
"%PYTHON_EXE%" app.py %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo SuperTory has stopped. Press any key to close this window.
pause >nul
exit /b %EXIT_CODE%
