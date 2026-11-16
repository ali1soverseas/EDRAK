# Start the EDRAK API through the Python entrypoint (edrak.main.run).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONPATH = Join-Path $root "backend\src"
& (Join-Path $root ".venv\Scripts\python.exe") -m edrak
