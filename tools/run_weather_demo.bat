@echo off
:: RainRoom3D/tools/run_weather_demo.bat (v1.4.1) — robust logging + pause on error
setlocal enabledelayedexpansion
pushd "%~dp0\.."
if not exist "out" mkdir "out"
set LOG="out\weather_demo_run.log"
echo [%DATE% %TIME%] Starting weather demo > %LOG%
REM Try main CLI first (fixed engines will work):
py -3 -m app.audio.engine --weather-demo --master-gain-db 18 1>>%LOG% 2>&1
set ERR=%ERRORLEVEL%
if NOT "!ERR!"=="0" (
  echo CLI failed with !ERR!, falling back to launcher >> %LOG%
  py -3 -m app.audio.run_weather_demo 1>>%LOG% 2>&1
  set ERR=%ERRORLEVEL%
)
echo [%DATE% %TIME%] Exit code: !ERR! >> %LOG%
type %LOG%
if NOT "!ERR!"=="0" (
  echo FAILED (exit !ERR!). See out\weather_demo_run.log
  pause
)
popd
endlocal
exit /b %ERR%
