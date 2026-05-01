@echo off
setlocal

set CONFIG=config.yaml
set OUTPUT=

:: Разбираем аргументы: run.bat [config.yaml] [output.xlsx]
if not "%~1"=="" set CONFIG=%~1
if not "%~2"=="" set OUTPUT=--output %~2

echo =====================================
echo  WF Grid Search
echo  Config:  %CONFIG%
if "%OUTPUT%"=="" (echo  Output:  ^(auto^)) else (echo  Output:  %~2)
echo =====================================
echo.

python run.py --config "%CONFIG%" %OUTPUT%

if errorlevel 1 (
    echo.
    echo [FAILED] Pipeline exited with error.
    pause
    exit /b 1
)

echo.
pause
endlocal
