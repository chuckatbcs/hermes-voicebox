# Launcher for Voicebox API Backend Server (Windows)
$ErrorActionPreference = "Stop"

$HERMES_DIR = "$HOME\.hermes"
$SERVER_SCRIPT = "$HERMES_DIR\scripts\voicebox_server.py"

Write-Host "=== Starting Voicebox API Backend Server ===" -ForegroundColor Cyan

if (-not (Test-Path $SERVER_SCRIPT)) {
    Write-Host "Error: $SERVER_SCRIPT not found. Please run install.ps1 first." -ForegroundColor Red
    exit 1
}

# Start backend as background process
$process = Start-Process -FilePath "python" -ArgumentList "`"$SERVER_SCRIPT`"" -PassThru -WindowStyle Hidden

Write-Host "Voicebox API Backend started (PID: $($process.Id))" -ForegroundColor Green
Write-Host "Listening on http://127.0.0.1:17493" -ForegroundColor Yellow
