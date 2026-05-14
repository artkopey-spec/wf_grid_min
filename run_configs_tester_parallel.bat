@echo off
setlocal
cd /d "%~dp0"
python run_configs_tester_parallel.py %*
exit /b %ERRORLEVEL%
