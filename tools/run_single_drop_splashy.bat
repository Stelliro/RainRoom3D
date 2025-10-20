@echo off
:: RainRoom3D/tools/run_single_drop_splashy.bat (v1.7.0) — clear splash audition
setlocal enabledelayedexpansion
pushd "%~dp0\.."
if not exist "out" mkdir "out"
set LOG="out\single_drop_splashy_run.log"
echo [%DATE% %TIME%] Rendering splashy single drop (water 3.5mm, +30dB, normalize; splash=-3dB, spray=-10dB, tail=90ms) > %LOG%
py -3 -m app.audio.engine --single-drop --surface water --size-mm 3.5 --master-gain-db 30 --normalize --splash-db -3 --spray-db -10 --spray-tail-ms 90 --wetness 1.0 --hp-cut 240 1>>%LOG% 2>&1
set ERR=%ERRORLEVEL%
type %LOG%
if NOT "!ERR!"=="0" (
  echo FAILED (exit !ERR!). See out\single_drop_splashy_run.log and out\engine_error.log
  pause
)
popd
endlocal
exit /b %ERR%
