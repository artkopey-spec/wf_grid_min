@echo off
setlocal enabledelayedexpansion

set CONFIG=config.yaml
set BACKUP=config.yaml.bak
set OUTPUT=

if not "%~1"=="" set OUTPUT=--output %~1

echo =====================================
echo  WF Grid Search
echo  Config:  %CONFIG%
echo  Anchor:  end
if "%OUTPUT%"=="" (echo  Output:  ^(auto^)) else (echo  Output:  %~1)
echo =====================================
echo.

:: Патчим anchor -> end
copy /Y "%CONFIG%" "%BACKUP%" >nul
powershell -Command "(Get-Content '%CONFIG%') -replace 'anchor:\s*\"start\"', 'anchor: \"end\"' | Set-Content '%CONFIG%'"

python run.py --config "%CONFIG%" %OUTPUT%
set EXITCODE=%ERRORLEVEL%

:: Восстанавливаем оригинальный конфиг
copy /Y "%BACKUP%" "%CONFIG%" >nul
del "%BACKUP%" >nul

if %EXITCODE% NEQ 0 (
    echo.
    echo [FAILED] Pipeline exited with error.
    pause
    exit /b %EXITCODE%
)

echo.
pause
endlocal
