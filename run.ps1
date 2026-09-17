$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv313\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    py -3.13 -m venv (Join-Path $PSScriptRoot ".venv313")
    & $python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
}
Push-Location $PSScriptRoot
try {
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
} finally {
    Pop-Location
}
