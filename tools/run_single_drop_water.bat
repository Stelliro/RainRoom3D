@echo off
:: RainRoom3D/tools/run_single_drop_water.bat (v1.5.3) — water audition helper
setlocal enabledelayedexpansion
pushd "%~dp0\.."
if not exist "out" mkdir "out"
set LOG="out\single_drop_water_run.log"
echo [%DATE% %TIME%] Rendering water single drop at +30 dB, normalized >> %LOG%
py -3 -m app.audio.engine --single-drop --surface water --size-mm 3.5 --master-gain-db 30 --normalize 1>>%LOG% 2>&1
set ERR=%ERRORLEVEL%
type %LOG%
if NOT "!ERR!"=="0" (
  echo FAILED (exit !ERR!). See out\single_drop_water_run.log and out\engine_error.log
  pause
)
popd
endlocal
exit /b %ERR%
