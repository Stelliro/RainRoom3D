@echo off
:: RainRoom3D/tools/run_single_drop_wet_v161.bat — darker, non‑hollow wet audition
setlocal enabledelayedexpansion
pushd "%~dp0\.."
if not exist "out" mkdir "out"
set LOG="out\single_drop_wet_v161_run.log"
echo [%DATE% %TIME%] Rendering wet single drop (water 3.5mm, +30dB, normalize, wetness=1.0, slap=-10dB, splat=-6dB, hp_cut=280Hz) > %LOG%
py -3 -m app.audio.engine --single-drop --surface water --size-mm 3.5 --master-gain-db 30 --normalize --wetness 1.0 --slap-db -10 --splat-db -6 --hp-cut 280 1>>%LOG% 2>&1
set ERR=%ERRORLEVEL%
type %LOG%
if NOT "!ERR!"=="0" (
  echo FAILED (exit !ERR!). See out\single_drop_wet_v161_run.log and out\engine_error.log
  pause
)
popd
endlocal
exit /b %ERR%
