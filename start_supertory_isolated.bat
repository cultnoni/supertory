@echo off
setlocal
set "SUPERTORY_DB_MODE=isolated"
call "%~dp0start_supertory.bat" %*
