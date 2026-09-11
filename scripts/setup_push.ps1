$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "Configure notification push. Secrets are saved only to local .env." -ForegroundColor Cyan
Write-Host "Push channel:"
Write-Host "1. Bark (iPhone notification, recommended)"
Write-Host "2. ServerChan (WeChat personal account)"
Write-Host "3. WeCom group bot"
Write-Host "4. Multiple channels"

$choice = Read-Host "Choose 1/2/3/4"
$channels = @()
$barkEndpoint = ""
$barkKey = ""
$barkServer = "https://api.day.app"
$serverchan = ""
$wecom = ""

if ($choice -eq "1" -or $choice -eq "4") {
    Write-Host "Open Bark on iPhone and copy the test URL, usually like https://api.day.app/xxxx/test." -ForegroundColor Yellow
    $barkInput = Read-Host "Paste Bark full test URL or Bark key"
    if ($barkInput.Trim()) {
        if ($barkInput.StartsWith("http")) {
            $barkEndpoint = ($barkInput.Trim() -replace "/[^/]*$", "")
        } else {
            $barkKey = $barkInput.Trim()
            $customServer = Read-Host "Custom Bark server URL, or press Enter for https://api.day.app"
            if ($customServer.Trim()) {
                $barkServer = $customServer.Trim()
            }
        }
        $channels += "bark"
    }
}

if ($choice -eq "2" -or $choice -eq "4") {
    $serverchan = Read-Host "Paste ServerChan SendKey"
    if ($serverchan.Trim()) {
        $channels += "serverchan"
    }
}

if ($choice -eq "3" -or $choice -eq "4") {
    $wecom = Read-Host "Paste WeCom group bot Webhook"
    if ($wecom.Trim()) {
        $channels += "wecom"
    }
}

if ($channels.Count -eq 0) {
    throw "No push channel configured."
}

$envContent = @(
    "BARK_ENDPOINT=$barkEndpoint",
    "BARK_KEY=$barkKey",
    "BARK_SERVER=$barkServer",
    "BARK_GROUP=stock-alerts",
    "BARK_LEVEL=",
    "BARK_SOUND=",
    "BARK_URL=",
    "SERVERCHAN_SENDKEY=$serverchan",
    "WECOM_BOT_WEBHOOK=$wecom",
    "PUSH_CHANNELS=$($channels -join ',')"
) -join "`n"

Set-Content -Path ".env" -Value $envContent -Encoding UTF8
Write-Host ".env created." -ForegroundColor Green
Write-Host "Next step: run python run.py --monitor to send a test notification."
