@echo off
:: RainRoom3D/tools/run_single_drop.bat (v1.5.2) — robust logging
setlocal enabledelayedexpansion
pushd "%~dp0\.."
if not exist "out" mkdir "out"
set LOG="out\single_drop_run.log"
echo [%DATE% %TIME%] Rendering single drop > %LOG%
py -3 -m app.audio.engine --single-drop 1>>%LOG% 2>&1
set ERR=%ERRORLEVEL%
echo [%DATE% %TIME%] Exit code: !ERR! >> %LOG%
type %LOG%
if NOT "!ERR!"=="0" (
  echo FAILED (exit !ERR!). See out\single_drop_run.log and out\engine_error.log
  pause
)
popd
endlocal
exit /b %ERR%
