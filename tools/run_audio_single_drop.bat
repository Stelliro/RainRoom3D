@echo off
:: RainRoom3D/tools/run_audio_single_drop.bat (v1.2)
setlocal enabledelayedexpansion
REM cd to repo root from tools/
pushd "%~dp0\.."
if not exist "out" mkdir "out"
set LOG="out\single_drop_run.log"
echo [%DATE% %TIME%] Starting single-drop render > %LOG%
py -3 -m app.audio.engine --single-drop 1>>%LOG% 2>&1
set ERR=%ERRORLEVEL%
echo [%DATE% %TIME%] Exit code: !ERR! >> %LOG%
type %LOG%
popd
endlocal
exit /b %ERR%
