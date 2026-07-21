# Windows installer for Hermes Voicebox integration
$ErrorActionPreference = "Stop"

$HERMES_DIR = "$HOME\.hermes"
Write-Host "=== Installing Hermes Voicebox Integration (Windows) ===" -ForegroundColor Cyan

# 1. Ensure target directories exist
New-Item -ItemType Directory -Force -Path "$HERMES_DIR\desktop-plugins\voice-switcher" | Out-Null
New-Item -ItemType Directory -Force -Path "$HERMES_DIR\scripts" | Out-Null

# 2. Copy plugin files
Write-Host "Copying plugin UI..."
Copy-Item -Path "desktop-plugin\plugin.js" -Destination "$HERMES_DIR\desktop-plugins\voice-switcher\plugin.js" -Force

# 3. Copy python bridge script
Write-Host "Copying python bridge..."
Copy-Item -Path "scripts\voicebox_tts.py" -Destination "$HERMES_DIR\scripts\voicebox_tts.py" -Force

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Green
Write-Host "Please add/replace the following section in your $HERMES_DIR\config.yaml:"
Write-Host ""
Write-Host "tts:"
Write-Host "  provider: voicebox"
Write-Host "  providers:"
Write-Host "    voicebox:"
Write-Host "      type: command"
Write-Host "      command: python $HERMES_DIR\scripts\voicebox_tts.py --text-file {input_path} --out {output_path} --voice {voice}"
Write-Host "      voice: default"
Write-Host "      output_format: wav"
Write-Host ""
Write-Host "Make sure your Voicebox API is running locally on port 17493!" -ForegroundColor Yellow
