# Capture one Office join in mitmweb WITHOUT filtering, then extract Twinkly traffic.
# The iPhone must already be configured to proxy via this PC on port 8080.
# This writes full potentially sensitive HTTP traffic to a private local folder.
param(
    [string]$OutputRoot = "C:\TwinklyCapture",
    [int]$Port = 8080,
    [string]$OfficeLeft = "192.168.21.90",
    [string]$OfficeRight = "192.168.21.93"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$scripts = Join-Path $env:LOCALAPPDATA "Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\Scripts"
$mitmweb = Join-Path $scripts "mitmweb.exe"
if (-not (Test-Path $mitmweb)) {
    $cmd = Get-Command mitmweb.exe -ErrorAction SilentlyContinue
    if ($cmd) { $mitmweb = $cmd.Source } else { throw "mitmweb.exe not found. Install mitmproxy or check Python Scripts path." }
}
if ($Port -lt 1024 -or $Port -gt 65535) { throw "Port must be 1024-65535" }
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$raw = Join-Path $OutputRoot "office-join-$timestamp-RAW-PRIVATE.flows"
$filtered = Join-Path $OutputRoot "office-join-$timestamp-twinkly-PRIVATE.flows"
if ((Test-Path $raw) -or (Test-Path $filtered)) { throw "Capture filename already exists" }
Write-Host "Starting UNFILTERED capture. Leave Twinkly app closed until mitmweb starts."
Write-Host "RAW capture: $raw"
Write-Host "Open Twinkly on iPhone, perform ONE Office join, then press Ctrl+C here."
Write-Host "After capture, turn OFF the iPhone's Wi-Fi manual proxy."
try {
    & $mitmweb --mode regular --listen-host 0.0.0.0 --listen-port $Port `
        --set web_host=127.0.0.1 `
        --set "save_stream_file=$raw"
}
finally {
    if (Test-Path $raw) {
        Write-Host "Extracting matching Twinkly + Office LAN flows (original preserved)."
        & python (Join-Path $repoRoot "filter_twinkly_flows.py") $raw $filtered --ips $OfficeLeft $OfficeRight
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Filtering failed; the RAW capture has been preserved. No capture was deleted."
        }
    } else {
        Write-Warning "No raw capture found. Did any proxied traffic reach mitmweb?"
    }
    Write-Host "Turn OFF the iPhone's manual proxy if it is still enabled."
}
