$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".env")) {
    throw "Missing .env. Please run scripts\setup_push.ps1 first."
}

python run.py --monitor
