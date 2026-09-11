$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "Configure Compass API. Leave blank to keep using local exports." -ForegroundColor Cyan
Write-Host "Endpoint template examples:"
Write-Host "  https://example.com/api/stock/{code}/summary"
Write-Host "  /stock/{code}/summary  plus COMPASS_API_BASE_URL"

$baseUrl = Read-Host "Compass API base URL, optional"
$endpoint = Read-Host "Compass API endpoint template"
$token = Read-Host "Compass API token, optional"
$timeout = Read-Host "Timeout seconds, press Enter for 15"

if (-not $timeout.Trim()) {
    $timeout = "15"
}

$envPath = ".env"
if (Test-Path $envPath) {
    $lines = Get-Content -Path $envPath -Encoding UTF8
} else {
    $lines = @()
}

function Set-EnvLine {
    param(
        [string[]]$Lines,
        [string]$Key,
        [string]$Value
    )
    $updated = $false
    $newLines = foreach ($line in $Lines) {
        if ($line -match "^$([regex]::Escape($Key))=") {
            $updated = $true
            "$Key=$Value"
        } else {
            $line
        }
    }
    if (-not $updated) {
        $newLines += "$Key=$Value"
    }
    return $newLines
}

$lines = Set-EnvLine $lines "COMPASS_API_BASE_URL" $baseUrl.Trim()
$lines = Set-EnvLine $lines "COMPASS_API_ENDPOINT_TEMPLATE" $endpoint.Trim()
$lines = Set-EnvLine $lines "COMPASS_API_TOKEN" $token.Trim()
$lines = Set-EnvLine $lines "COMPASS_API_TIMEOUT" $timeout.Trim()

Set-Content -Path $envPath -Value ($lines -join "`n") -Encoding UTF8
Write-Host ".env updated." -ForegroundColor Green
Write-Host "Test with: python run.py --dry-run"
