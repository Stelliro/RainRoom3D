# Zip a clean source tree for GitHub Releases (no .git, venv, out, logs).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$OutDir = Join-Path $Root "dist"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd"
$Zip = Join-Path $OutDir "RainRoom3D-source-$Stamp.zip"

if (Test-Path $Zip) { Remove-Item $Zip -Force }

$Exclude = @(
    ".git", ".venv", "venv", "dist", "build", "out", "logs",
    "__pycache__", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    "node_modules"
)
$Temp = Join-Path $env:TEMP "RainRoom3D_src_pack"
if (Test-Path $Temp) { Remove-Item $Temp -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Temp | Out-Null

# Exclude build artifacts / caches; keep app source lean for Releases
robocopy $Root $Temp /E /XD $Exclude /XF "*.pyc" "*.log" "*.spec" "*.zip" /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
Compress-Archive -Path (Join-Path $Temp "*") -DestinationPath $Zip -Force
Remove-Item $Temp -Recurse -Force
Write-Host "Wrote $Zip"
Write-Output $Zip
