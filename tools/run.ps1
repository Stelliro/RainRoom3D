param([int]$SampleRate=48000, [int]$Blocksize=512)
$env:RAINROOM_SAMPLERATE = "$SampleRate"
$env:RAINROOM_BLOCKSIZE = "$Blocksize"

if (!(Test-Path ".venv")) { python -m venv .venv }
. .\.venv\Scripts\Activate.ps1
if (!(Test-Path ".venv\.deps_ok")) {
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  "ok" | Out-File ".venv\.deps_ok" -Encoding ascii -Force
}
python -m app.main
