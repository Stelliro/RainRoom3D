@echo off
:: Start RainRoom3D from source (installs requirements the first time).
setlocal
pushd "%~dp0"
set "PY=python"
where py >nul 2>&1 && set "PY=py -3"
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || goto :fail
)
if not exist ".venv\.deps_ok" (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
  echo ok> ".venv\.deps_ok"
)
".venv\Scripts\python.exe" -m app.main %*
set "ERR=%ERRORLEVEL%"
popd
exit /b %ERR%
:fail
echo Could not set up Python. Install Python 3.10+ from python.org and try again.
pause
popd
exit /b 1
