@echo off
setlocal

cd /d "%~dp0"

set "CONFIGS_DIR=config"
set "OUTPUT_DIR=results\batch"
set "GLOB=*.yaml"
set "PYTHON_EXE=python"
set "DEFAULT_ARGS=--configs-dir "%CONFIGS_DIR%" --output-dir "%OUTPUT_DIR%" --glob "%GLOB%""

echo ==============================
echo  WF Grid Batch Runner
echo  Script: run_configs_batch.py
echo  Configs: %CONFIGS_DIR%\%GLOB%
echo  Output:  %OUTPUT_DIR%
echo ==============================
echo.

if not exist "run_configs_batch.py" (
    echo Error: run_configs_batch.py not found in %CD%
    pause
    exit /b 1
)

if not exist "%CONFIGS_DIR%" (
    echo Error: configs directory not found: %CONFIGS_DIR%
    pause
    exit /b 1
)

if "%~1"=="" (
    %PYTHON_EXE% run_configs_batch.py %DEFAULT_ARGS%
) else (
    %PYTHON_EXE% run_configs_batch.py %*
)

set "EXITCODE=%ERRORLEVEL%"

if %EXITCODE% NEQ 0 (
    echo.
    echo [FAILED] Batch exited with error code %EXITCODE%.
    pause
    exit /b %EXITCODE%
)

echo.
echo [OK] Batch completed successfully.
pause
exit /b 0
