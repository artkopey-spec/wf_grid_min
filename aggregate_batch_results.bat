@echo off
setlocal
cd /d "%~dp0"
python "scripts\aggregate_batch_results.py"
set "EXITCODE=%ERRORLEVEL%"
pause
endlocal & exit /b %EXITCODE%
