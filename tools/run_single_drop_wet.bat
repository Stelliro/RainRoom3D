@echo off
:: RainRoom3D/tools/run_single_drop_wet.bat (v1.6.0) — wet slap audition helper
setlocal enabledelayedexpansion
pushd "%~dp0\.."
if not exist "out" mkdir "out"
set LOG="out\single_drop_wet_run.log"
echo [%DATE% %TIME%] Rendering WET single drop (water, size 3.5mm, +30dB, normalize, wetness=1.0, slap=-6dB) > %LOG%
py -3 -m app.audio.engine --single-drop --surface water --size-mm 3.5 --master-gain-db 30 --normalize --wetness 1.0 --slap-db -6 1>>%LOG% 2>&1
set ERR=%ERRORLEVEL%
type %LOG%
if NOT "!ERR!"=="0" (
  echo FAILED (exit !ERR!). See out\single_drop_wet_run.log and out\engine_error.log
  pause
)
popd
endlocal
exit /b %ERR%
