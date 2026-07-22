# Windows installer for Hermes Voicebox integration & backend service
$ErrorActionPreference = "Stop"

$HERMES_DIR = "$HOME\.hermes"
Write-Host "=== Installing Hermes Voicebox Integration & Backend (Windows) ===" -ForegroundColor Cyan

# 1. Ensure target directories exist
New-Item -ItemType Directory -Force -Path "$HERMES_DIR\desktop-plugins\voice-switcher" | Out-Null
New-Item -ItemType Directory -Force -Path "$HERMES_DIR\scripts" | Out-Null
New-Item -ItemType Directory -Force -Path "$HERMES_DIR\voicebox_profiles" | Out-Null

# 2. Copy plugin UI
Write-Host "Copying plugin UI..."
Copy-Item -Path "desktop-plugin\plugin.js" -Destination "$HERMES_DIR\desktop-plugins\voice-switcher\plugin.js" -Force

# 3. Copy python bridge script and backend server
Write-Host "Copying python bridge & backend server scripts..."
Copy-Item -Path "scripts\voicebox_tts.py" -Destination "$HERMES_DIR\scripts\voicebox_tts.py" -Force
Copy-Item -Path "scripts\voicebox_server.py" -Destination "$HERMES_DIR\scripts\voicebox_server.py" -Force

# 4. Install Python dependencies
Write-Host "Checking Python dependencies (fastapi, uvicorn, numpy)..."
try {
    python -c "import fastapi, uvicorn, numpy" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing required backend packages via pip..." -ForegroundColor Yellow
        pip install fastapi uvicorn numpy
    }
} catch {
    Write-Host "Installing required backend packages via pip..." -ForegroundColor Yellow
    pip install fastapi uvicorn numpy
}

# 5. Automatically patch $HERMES_DIR\config.yaml if present
$CONFIG_PATH = "$HERMES_DIR\config.yaml"
if (Test-Path $CONFIG_PATH) {
    Write-Host "Configuring $CONFIG_PATH..."
    $config = Get-Content $CONFIG_PATH -Raw
    if ($config -notmatch "provider:\s*voicebox") {
        # Update provider setting
        $config = $config -replace "(?m)^(\s*)provider:\s*\w+", "`$1provider: voicebox"
    }
    if ($config -notmatch "voicebox:") {
        # Add voicebox provider definition under providers
        $voiceboxBlock = @"
    voicebox:
      type: command
      command: python `$env:USERPROFILE\.hermes\scripts\voicebox_tts.py --text-file {input_path} --out {output_path} --voice {voice}
      voice: default
      output_format: wav
"@
        $config = $config -replace "(?m)^(\s*providers:\s*`$)", "`$1`n$voiceboxBlock"
    }
    Set-Content -Path $CONFIG_PATH -Value $config
}

# 6. Check if server is already running on port 17493, otherwise launch it
try {
    $response = python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:17493/health', timeout=2).status)" 2>$null
    if ($response -eq "200") {
        Write-Host "Voicebox API Backend Server is already active on http://127.0.0.1:17493" -ForegroundColor Green
    } else {
        Write-Host "Launching Voicebox API Backend Server..." -ForegroundColor Yellow
        Start-Process -FilePath "python" -ArgumentList "`"$HERMES_DIR\scripts\voicebox_server.py`"" -WindowStyle Hidden
    }
} catch {
    Write-Host "Launching Voicebox API Backend Server..." -ForegroundColor Yellow
    Start-Process -FilePath "python" -ArgumentList "`"$HERMES_DIR\scripts\voicebox_server.py`"" -WindowStyle Hidden
}

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Green
Write-Host "Voicebox API backend is running on http://127.0.0.1:17493" -ForegroundColor Cyan
Write-Host "Hermes Desktop plugin & TTS bridge are fully installed." -ForegroundColor Cyan
