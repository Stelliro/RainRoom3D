@echo off
:: run_ab.bat — renders 6 extreme variants to verify params actually change sound (no white noise).
setlocal enabledelayedexpansion
pushd "%~dp0"
if not exist "out\ab" mkdir "out\ab"
set "LOG=out\run_ab.log"
echo [%DATE% %TIME%] START (run_ab) > "%LOG%"
set "PYCMD="
where py >nul 2>&1 && set "PYCMD=py -3"
if "%PYCMD%"=="" ( where python >nul 2>&1 && set "PYCMD=python" )
if "%PYCMD%"=="" (
  echo [run_ab] ERROR: Python not found >> "%LOG%"
  type "%LOG%"
  echo.
  pause
  popd & endlocal & exit /b 1
)
echo [run_ab] Using %PYCMD% >> "%LOG%"
%PYCMD% --version >> "%LOG%" 2>&1
echo [run_ab] rendering variants... >> "%LOG%"
%PYCMD% "tools\ab_render.py" 1>>"%LOG%" 2>&1
echo [run_ab] done. See out\ab\ab_report.txt >> "%LOG%"
type "%LOG%"
echo.
pause
popd
endlocal
