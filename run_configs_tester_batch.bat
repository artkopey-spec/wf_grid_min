@echo off
setlocal

cd /d "%~dp0"

if "%~1"=="" (
    python run_configs_tester_batch.py
) else (
    python run_configs_tester_batch.py %*
)

exit /b %ERRORLEVEL%
