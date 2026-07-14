# Build a portable Windows folder with PyInstaller (lean — no CUDA / ML stack).
# Requires: pip install pyinstaller
#
# Your global Python may have cupy/torch/etc. installed. PyInstaller will try to
# bag those unless we exclude them. This script excludes known bloat and then
# strips leftover CUDA DLLs after the collect step.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Installing/ensuring PyInstaller..."
python -m pip install -q "pyinstaller>=6.0"

$Name = "RainRoom3D"
$Entry = "app/main.py"
$DistDir = Join-Path $Root "dist"
$Onedir = Join-Path $DistDir $Name
$BuildDir = Join-Path $Root "build"

# Fresh output (keeps other zips under dist/)
if (Test-Path $Onedir) {
    Write-Host "Removing previous $Onedir ..."
    Remove-Item -Recurse -Force $Onedir
}
if (Test-Path $BuildDir) {
    Write-Host "Removing previous build/ ..."
    Remove-Item -Recurse -Force $BuildDir
}

# Data folders to bundle
$AddData = @(
    "configs;configs",
    "assets;assets",
    "docs/media;docs/media"
)

# Heavy / unused packages often present in a workstation Python env
$Exclude = @(
    "cupy", "cupyx", "cupy_backends",
    "numba", "llvmlite",
    "torch", "torchvision", "torchaudio",
    "tensorflow", "tensorboard", "keras",
    "cv2", "opencv",
    "sklearn", "scikit_learn",
    "pandas", "pyarrow", "polars",
    "matplotlib", "mpl_toolkits", "matplotlib_inline", "contourpy", "kiwisolver", "cycler", "fontTools",
    "IPython", "jupyter", "notebook", "nbformat", "nbconvert",
    "pytest", "unittest", "_pytest",
    "black", "mypy", "ruff",
    "sympy", "networkx",
    "h5py", "tables",
    "bokeh", "plotly", "dash",
    "wx", "tkinter", "turtle"
)

$args = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", $Name,
    "--paths", $Root,
    # Don't pull every binary from site-packages just because something mentions it
    "--noupx"
)
foreach ($d in $AddData) {
    $args += @("--add-data", $d)
}
foreach ($hi in @(
    "scipy.signal",
    "sounddevice",
    "OpenGL",
    "OpenGL.GL",
    "OpenGL.GLU",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets"
)) {
    $args += @("--hidden-import", $hi)
}
foreach ($ex in $Exclude) {
    $args += @("--exclude-module", $ex)
}
$args += $Entry

Write-Host "Running: python -m PyInstaller (lean excludes) ..."
python -m PyInstaller @args

$Exe = Join-Path $Onedir "$Name.exe"
if (-not (Test-Path $Exe)) {
    $found = Get-ChildItem -Path $DistDir -Recurse -Filter "$Name.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $found) { throw "Build failed: $Name.exe not found under dist/" }
    $Exe = $found.FullName
    $Onedir = Split-Path $Exe -Parent
}
Write-Host "Built: $Exe"

# ---- Post-strip: CUDA / ML leftovers that snuck in via DLL dependency scan ----
function Remove-BloatFromTree([string]$Path) {
    if (-not (Test-Path $Path)) { return 0 }
    $patterns = @(
        "cublas*.dll", "cufft*.dll", "curand*.dll", "cusolver*.dll", "cusparse*.dll",
        "nvrtc*.dll", "nvJitLink*.dll", "nvcuda*.dll", "cudart*.dll", "cudnn*.dll",
        "cuTENSOR*.dll", "nvToolsExt*.dll", "cuda*.dll"
    )
    $removed = 0L
    $internal = Join-Path $Path "_internal"
    $scanRoots = @($Path)
    if (Test-Path $internal) { $scanRoots += $internal }

    foreach ($root in $scanRoots) {
        foreach ($pat in $patterns) {
            Get-ChildItem -Path $root -Recurse -File -Filter $pat -ErrorAction SilentlyContinue | ForEach-Object {
                $removed += $_.Length
                Write-Host "  strip DLL $($_.Name) ($([math]::Round($_.Length/1MB,1)) MB)"
                Remove-Item -Force $_.FullName
            }
        }
        # Entire package trees
        foreach ($dirName in @("cupy", "cupyx", "cupy_backends", "torch", "tensorflow", "matplotlib", "mpl_toolkits", "contourpy", "sklearn", "pandas", "numba", "cv2")) {
            Get-ChildItem -Path $root -Recurse -Directory -Filter $dirName -ErrorAction SilentlyContinue | ForEach-Object {
                $sz = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
                if ($sz) { $removed += $sz }
                Write-Host "  strip dir $($_.FullName.Substring($Path.Length)) ($([math]::Round(($sz/1MB),1)) MB)"
                Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue
            }
        }
    }
    return $removed
}

Write-Host "Stripping CUDA / ML bloat from collect tree..."
$stripped = Remove-BloatFromTree $Onedir
Write-Host ("Stripped {0:N1} MB of bloat" -f ($stripped / 1MB))

# Ship license + readme next to exe
Copy-Item -Force (Join-Path $Root "LICENSE") (Join-Path $Onedir "LICENSE") -ErrorAction SilentlyContinue
Copy-Item -Force (Join-Path $Root "README.md") (Join-Path $Onedir "README.md") -ErrorAction SilentlyContinue
# Ensure configs/assets exist at top level too (some launches use cwd)
Copy-Item -Force -Recurse (Join-Path $Root "configs") (Join-Path $Onedir "configs") -ErrorAction SilentlyContinue
Copy-Item -Force -Recurse (Join-Path $Root "assets") (Join-Path $Onedir "assets") -ErrorAction SilentlyContinue

$total = (Get-ChildItem $Onedir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
Write-Host ("Portable folder size: {0:N1} MB  ($Onedir)" -f ($total / 1MB))

# Zip for GitHub Releases
$ZipName = "RainRoom3D-windows-portable.zip"
$ZipPath = Join-Path $DistDir $ZipName
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Write-Host "Zipping $ZipPath ..."
# Compress-Archive can choke on huge trees; use .NET for reliability
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($Onedir, $ZipPath, [System.IO.Compression.CompressionLevel]::Optimal, $true)
$zipSize = (Get-Item $ZipPath).Length
Write-Host ("Zip size: {0:N1} MB" -f ($zipSize / 1MB))

if ($total -gt 800MB) {
    Write-Warning "Portable build still > 800 MB. Check dist\$Name\_internal for unexpected packages."
}

Write-Host "Done."
Write-Host "  Folder: $Onedir"
Write-Host "  Zip:    $ZipPath"
Write-Output $ZipPath
