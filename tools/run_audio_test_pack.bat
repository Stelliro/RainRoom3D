@echo off
:: RainRoom3D/tools/run_audio_test_pack.bat (v1.3)
setlocal enabledelayedexpansion
pushd "%~dp0\.."
if not exist "out" mkdir "out"
echo Rendering test pack to out\test_pack ...
py -3 -m app.audio.engine --test-pack 1>"out\test_pack_run.log" 2>&1
set ERR=%ERRORLEVEL%
type "out\test_pack_run.log"
popd
endlocal
exit /b %ERR%
