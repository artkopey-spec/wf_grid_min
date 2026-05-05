@echo off
setlocal

cd /d "%~dp0"

set CSV=data.csv
set CONFIG=%~dp0config_tester.yaml
set OUT=result_single.xlsx

if not "%~1"=="" set CSV=%~1
if not "%~2"=="" set OUT=%~2

if not exist "%CSV%" (
    echo Error: CSV file not found: %CSV%
    pause
    exit /b 1
)

if not exist "%CONFIG%" (
    echo Error: Config file not found: %CONFIG%
    pause
    exit /b 1
)

set "PYTHONPATH=%~dp0donor;%PYTHONPATH%"

python -m supertrend_optimizer.cli.tester --csv "%CSV%" --config "%CONFIG%" --out "%OUT%"

if errorlevel 1 (
    echo.
    echo SINGLE RUN FAILED
    pause
    exit /b 1
)

echo.
echo SINGLE RUN COMPLETED
pause
exit /b 0
