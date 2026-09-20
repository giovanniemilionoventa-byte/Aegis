# Start Aegis on your own Windows PC with one command.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
#
# Needs only Docker Desktop, running. It creates .env with fresh secrets the
# first time (no Python, no bash), builds the stack, and opens the dashboard.

# Windows PowerShell 5.1 turns anything a native command writes to stderr into a
# terminating error when this is "Stop", and docker writes its progress there. So
# success is decided by the exit code, checked explicitly below.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Fail($message) {
    Write-Host $message -ForegroundColor Red
    exit 1
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker Desktop was not found. Install it from https://www.docker.com/products/docker-desktop and run this again."
}
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Fail "Docker Desktop is not running. Open it, wait until it says 'running', and run this again."
}

function New-Secret {
    $bytes = New-Object byte[] 33
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')
}

if (-not (Test-Path .env)) {
    $lines = @(
        "# Written by scripts/start.ps1. Never commit this file (it is gitignored).",
        "AEGIS_SECRET_KEY=$(New-Secret)",
        "AEGIS_EAT_KEY=$(New-Secret)",
        "AEGIS_EVIDENCE_SECRET_KEY=$(New-Secret)",
        "AEGIS_INTERNAL_GATEWAY_TOKEN=$(New-Secret)",
        "AEGIS_INTERNAL_TOOL_TOKEN=$(New-Secret)",
        "AEGIS_CRM_SECRET=$(New-Secret)",
        "AEGIS_VERIFY_AGENT_TOKEN=aegis_$(New-Secret)",
        "AEGIS_GMAIL_AGENT_TOKEN=aegis_$(New-Secret)",
        "AEGIS_OAUTH_ENCRYPTION_KEY=$(New-Secret)"
    )
    # UTF-8 without BOM: docker compose reads .env as plain text.
    [System.IO.File]::WriteAllLines((Join-Path (Get-Location) ".env"), $lines)
    Write-Host "Created .env with fresh secrets."
}

# Progress goes to stderr; show it as plain text instead of red error records.
docker compose up -d --build 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -ne 0) { Fail "docker compose failed. Read the messages above." }

Write-Host ""
Write-Host "Aegis is up: http://localhost:8000"
Write-Host "Demo login:  admin@acme.test / aegis-demo   (local demo only)"
Start-Process "http://localhost:8000"
