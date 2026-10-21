# Zip a clean source tree for GitHub Releases (no .git, venv, out, logs).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$OutDir = Join-Path $Root "dist"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Set-Location $Root
$Version = (python -c "from app.version import __version__; print(__version__)").Trim()
if (-not $Version) { throw "Could not read app/version.py" }
$Zip = Join-Path $OutDir "RainRoom3D-$Version-source.zip"

if (Test-Path $Zip) { Remove-Item $Zip -Force }

$Exclude = @(
    ".git", ".venv", "venv", "dist", "build", "out", "logs",
    "__pycache__", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    "node_modules", "backups"
)
$Temp = Join-Path $env:TEMP "RainRoom3D_src_pack"
if (Test-Path $Temp) { Remove-Item $Temp -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Temp | Out-Null

# This PC's phone settings (pairing key, sharing on/off) and phone rooms never ship
$Exclude += (Join-Path $Root "configs\zones")
# Nor this PC's own room (the autosave) and saved presets
foreach ($kind in @("house", "mix", "weather")) { $Exclude += (Join-Path $Root "configs\presets\$kind") }

# Exclude build artifacts / caches; keep app source lean for Releases
robocopy $Root $Temp /E /XD $Exclude /XF "*.pyc" "*.log" "*.spec" "*.zip" "remote.json" "my_house*.json" "RELEASE_v*-wip.md" /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
Compress-Archive -Path (Join-Path $Temp "*") -DestinationPath $Zip -Force
Remove-Item $Temp -Recurse -Force
Write-Host "Wrote $Zip"
Write-Output $Zip
