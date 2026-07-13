# Build a portable Windows folder with PyInstaller (optional).
# Requires: pip install pyinstaller
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Installing/ensuring PyInstaller..."
python -m pip install -q "pyinstaller>=6.0"

$Name = "RainRoom3D"
$Entry = "app/main.py"
$Dist = Join-Path $Root "dist\$Name"

# Data folders to bundle
$AddData = @(
    "configs;configs",
    "assets;assets",
    "docs/media;docs/media"
)

$args = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", $Name,
    "--paths", $Root
)
foreach ($d in $AddData) {
    $args += @("--add-data", $d)
}
# Hidden imports that PyInstaller often misses
foreach ($hi in @("scipy.signal", "sounddevice", "OpenGL", "OpenGL.GL", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets")) {
    $args += @("--hidden-import", $hi)
}
$args += $Entry

Write-Host "Running: python -m PyInstaller $($args -join ' ')"
python -m PyInstaller @args

if (-not (Test-Path (Join-Path $Dist "$Name.exe"))) {
    # onedir layout
    $Exe = Get-ChildItem -Path (Join-Path $Root "dist") -Recurse -Filter "$Name.exe" | Select-Object -First 1
    if (-not $Exe) { throw "Build failed: $Name.exe not found under dist/" }
    Write-Host "Built: $($Exe.FullName)"
} else {
    Write-Host "Built: $Dist\$Name.exe"
}

# Copy default configs next to exe if onedir
$Onedir = Join-Path $Root "dist\$Name"
if (Test-Path $Onedir) {
    Copy-Item -Force -Recurse (Join-Path $Root "configs") (Join-Path $Onedir "configs") -ErrorAction SilentlyContinue
    Copy-Item -Force -Recurse (Join-Path $Root "assets") (Join-Path $Onedir "assets") -ErrorAction SilentlyContinue
    Copy-Item -Force (Join-Path $Root "LICENSE") (Join-Path $Onedir "LICENSE") -ErrorAction SilentlyContinue
    Copy-Item -Force (Join-Path $Root "README.md") (Join-Path $Onedir "README.md") -ErrorAction SilentlyContinue
}

Write-Host "Done. Package under dist\"
