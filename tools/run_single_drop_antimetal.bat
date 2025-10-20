@echo off
:: RainRoom3D/tools/run_single_drop_antimetal.bat (v1.7.1) — anti‑clang water audition
setlocal enabledelayedexpansion
pushd "%~dp0\.."
if not exist "out" mkdir "out"
set LOG="out\single_drop_antimetal_run.log"
echo [%DATE% %TIME%] Rendering anti‑metal water drop (3.5mm, +30dB, normalize; hp_cut=320, antimetal=1, diffuse_g=0) > %LOG%
py -3 -m app.audio.engine --single-drop --surface water --size-mm 3.5 --master-gain-db 30 --normalize --wetness 1.0 --hp-cut 320 --antimetal 1 --diffuse-g 0 1>>%LOG% 2>&1
set ERR=%ERRORLEVEL%
type %LOG%
if NOT "!ERR!"=="0" (
  echo FAILED (exit !ERR!). See out\single_drop_antimetal_run.log and out\engine_error.log
  pause
)
popd
endlocal
exit /b %ERR%
