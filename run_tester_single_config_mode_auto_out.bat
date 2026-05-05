@echo off
setlocal

cd /d "%~dp0"

set "CSV=data.csv"
set "CONFIG=%~dp0config_tester.yaml"
set "OUT="

if not "%~1"=="" set "CSV=%~1"
if not "%~2"=="" set "OUT=%~2"

if not exist "%CSV%" (
    echo Error: CSV file not found: %CSV%
    exit /b 1
)

if not exist "%CONFIG%" (
    echo Error: Config file not found: %CONFIG%
    exit /b 1
)

if "%OUT%"=="" (
    for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%I"
    set "OUT=result_single_%TS%.xlsx"
)

set "PYTHONPATH=%~dp0donor;%PYTHONPATH%"

echo Running single tester with config mode...
echo   CSV:    %CSV%
echo   CONFIG: %CONFIG%
echo   OUT:    %OUT%

python -m supertrend_optimizer.cli.tester --csv "%CSV%" --config "%CONFIG%" --out "%OUT%"
if errorlevel 1 (
    echo.
    echo SINGLE RUN FAILED
    exit /b 1
)

echo.
echo SINGLE RUN COMPLETED
echo Result file: %OUT%
exit /b 0
