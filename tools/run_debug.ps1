$ErrorActionPreference = "Continue"
if (!(Test-Path "logs")) { New-Item -ItemType Directory -Path logs | Out-Null }
$env:RAINROOM_NOGPU="0"
.\run.bat *> logs\app.log
Write-Host "Logs written to logs\app.log"
