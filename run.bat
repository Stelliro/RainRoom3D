@echo off
:: run.bat — v8 OVERWRITE (no inline Python/parens; stable on CMD)
:: Modes:
::   (default)       -> launch editor
::   --single-drop   -> render audio only
::   --both          -> render audio, then launch editor
:: NO WHITE NOISE is enforced in engine; this script just orchestrates.
setlocal enabledelayedexpansion
pushd "%~dp0"

echo [run v8] starting...
if not exist "out" mkdir "out"
set "LOG=out\run.log"
echo [%DATE% %TIME%] START (run v8) > "%LOG%"

:: Parse mode
set "MODE=editor"
if /I "%~1"=="--single-drop" set "MODE=audio"
if /I "%~1"=="--both"        set "MODE=both"
if /I "%~1"=="--editor"      set "MODE=editor"

:: Python
set "PYCMD="
where py >nul 2>&1 && set "PYCMD=py -3"
if "%PYCMD%"=="" ( where python >nul 2>&1 && set "PYCMD=python" )
if "%PYCMD%"=="" (
  echo [run v8] ERROR: Python not found on PATH >> "%LOG%"
  type "%LOG%"
  echo.
  pause
  popd & endlocal & exit /b 1
)
echo [run v8] Using %PYCMD% >> "%LOG%"
%PYCMD% --version >> "%LOG%" 2>&1

:: Ensure engine exists (audio path only)
if not exist "app\audio\engine.py" (
  echo [run v8] WARN: app\audio\engine.py not found. Audio path may fail. >> "%LOG%"
) else (
  :: Patch NumPy dtype bug if present (idempotent)
  powershell -NoProfile -Command "$p='app/audio/engine.py'; if(Test-Path $p){$t=Get-Content $p -Raw; $n=$t -replace 'np\.arange\(\s*N\s*,\s*np\.float32\s*\)', 'np.arange(N, dtype=np.float32)'; if($n -ne $t){$n | Set-Content $p -Encoding UTF8; '[run v8] Patched dtype bug in engine.py' | Out-File -Append 'out/run.log'}}"
)

set "PYTHONPATH=%CD%;%PYTHONPATH%"

if "%MODE%"=="audio"  goto :audio
if "%MODE%"=="both"   goto :audio_then_editor
goto :editor

:audio
set "ALOG=out\run_single_drop.log"
echo [%DATE% %TIME%] AUDIO START (run v8) > "%ALOG%"

:: CLI attempt
set "ARGS=-m app.audio.engine --single-drop --surface water --size-mm 3.5 --master-gain-db 30 --normalize --wetness 1.0 --hp-cut 130 --declick 1 --roundness 2.4 --attack-ms 18 --antimetal 1 --diffuse-g 0.08 --plop-db -4 --slap-db -36 --splat-db -20 --splash-db +1 --spray-db -11 --spray-tail-ms 130 --predelay-ms 2.6"
echo CMD1: %PYCMD% %ARGS% >> "%ALOG%"
%PYCMD% %ARGS% 1>>"%ALOG%" 2>&1
set "ERR1=%ERRORLEVEL%"

if not exist "out\single_drop.wav" (
  echo [run v8] CLI produced no file; using helper... >> "%ALOG%"
  %PYCMD% "tools\single_drop_helper.py" 1>>"%ALOG%" 2>&1
  set "ERR2=%ERRORLEVEL%"
) else (
  set "ERR2=0"
)

if exist "out\engine_error.log" (
  echo ---- engine_error.log ---- >> "%ALOG%"
  type "out\engine_error.log" >> "%ALOG%"
)

if exist "out\single_drop.wav" (
  for %%A in ("out\single_drop.wav") do echo Render complete: %%~fA  (%%~zA bytes) >> "%ALOG%"
) else (
  echo [run v8] Expected output not found: out\single_drop.wav >> "%ALOG%"
  echo [run v8] Exit codes: CLI=%ERR1%, Helper=%ERR2% >> "%ALOG%"
)

type "%ALOG%"
echo.
if "%MODE%"=="audio" goto :finish
goto :editor

:audio_then_editor
call :audio
goto :editor

:editor
set "ELOG=out\run_editor.log"
echo [%DATE% %TIME%] EDITOR START (run v8) > "%ELOG%"

if exist "bin\RainRoom3D.exe" (
  echo [run v8] starting bin\RainRoom3D.exe >> "%ELOG%"
  start "" "bin\RainRoom3D.exe"
  goto :editor_done
)

set "EDCMD="
if exist "app\main.py" set "EDCMD=%PYCMD% -m app.main"
if "%EDCMD%"=="" if exist "app\editor\__main__.py" set "EDCMD=%PYCMD% -m app.editor"
if "%EDCMD%"=="" if exist "app\ui\__main__.py" set "EDCMD=%PYCMD% -m app.ui"

if "%EDCMD%"=="" (
  echo [run v8] ERROR: Could not locate a 3D editor entry point. >> "%ELOG%"
  echo [run v8] Looked for: bin\RainRoom3D.exe, app\editor\^(__main__.py^|main.py^), app\ui\^(__main__.py^|main.py^), app\main.py >> "%ELOG%"
) else (
  echo [run v8] starting: %EDCMD% >> "%ELOG%"
  start "" %EDCMD%
)

:editor_done
type "%ELOG%"
echo.
goto :finish

:finish
echo [%DATE% %TIME%] END (run v8) >> "%LOG%"
type "%LOG%"
echo.
pause
popd
endlocal
