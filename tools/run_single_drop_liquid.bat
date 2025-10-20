@echo off
:: RainRoom3D/tools/run_single_drop_liquid.bat (v1.7.2) — anti‑click liquid splash
setlocal enabledelayedexpansion
pushd "%~dp0\.."
if not exist "out" mkdir "out"
set LOG="out\single_drop_liquid_run.log"
echo [%DATE% %TIME%] Rendering liquid anti‑click water drop (3.5mm, +30dB, normalize; hp_cut=220, antimetal=1, declick=1, roundness=1.2) > %LOG%
py -3 -m app.audio.engine --single-drop --surface water --size-mm 3.5 --master-gain-db 30 --normalize --wetness 1.0 --hp-cut 220 --antimetal 1 --diffuse-g 0 --declick 1 --roundness 1.2 1>>%LOG% 2>&1
set ERR=%ERRORLEVEL%
type %LOG%
if NOT "!ERR!"=="0" (
  echo FAILED (exit !ERR!). See out\single_drop_liquid_run.log and out\engine_error.log
  pause
)
popd
endlocal
exit /b %ERR%
