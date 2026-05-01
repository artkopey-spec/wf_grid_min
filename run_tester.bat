@echo off
setlocal

set CSV=data.csv
set CONFIG=config_tester.yaml

if not "%~1"=="" set CSV=%~1

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

python run_batch_tester.py --csv "%CSV%" --config "%CONFIG%"

if errorlevel 1 (
    echo.
    echo BATCH FAILED
    pause
    exit /b 1
)

echo.
echo BATCH COMPLETED
pause
exit /b 0
