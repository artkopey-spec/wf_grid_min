@echo off
setlocal
cd /d "%~dp0"
python "scripts\aggregate_tester_results.py"
set "EXITCODE=%ERRORLEVEL%"
pause
endlocal & exit /b %EXITCODE%

