#!/bin/bash
# Linux installer for Hermes Voicebox integration & backend service
set -e

HERMES_DIR="$HOME/.hermes"
echo "=== Installing Hermes Voicebox Integration & Backend (Linux) ==="

# 1. Ensure target directories exist
mkdir -p "$HERMES_DIR/desktop-plugins/voice-switcher"
mkdir -p "$HERMES_DIR/scripts"
mkdir -p "$HERMES_DIR/voicebox_profiles"

# 2. Copy plugin files
echo "Copying plugin UI..."
cp desktop-plugin/plugin.js "$HERMES_DIR/desktop-plugins/voice-switcher/plugin.js"

# 3. Copy python bridge script and backend server
echo "Copying python bridge & backend server scripts..."
cp scripts/voicebox_tts.py "$HERMES_DIR/scripts/voicebox_tts.py"
cp scripts/voicebox_server.py "$HERMES_DIR/scripts/voicebox_server.py"
chmod +x "$HERMES_DIR/scripts/voicebox_tts.py"
chmod +x "$HERMES_DIR/scripts/voicebox_server.py"

# 4. Check dependencies
echo "Checking Python dependencies (fastapi, uvicorn, numpy)..."
if ! python3 -c "import fastapi, uvicorn, numpy" &>/dev/null; then
    echo "Installing required backend packages via pip..."
    pip3 install fastapi uvicorn numpy
fi

# 5. Launch backend server if not running
if curl -s http://127.0.0.1:17493/health &>/dev/null; then
    echo "Voicebox API Backend Server is already active on http://127.0.0.1:17493"
else
    echo "Launching Voicebox API Backend Server..."
    python3 "$HERMES_DIR/scripts/voicebox_server.py" &>/dev/null &
fi

echo ""
echo "=== Setup Complete ==="
echo "Voicebox API backend is running on http://127.0.0.1:17493"
echo "Hermes Desktop plugin & TTS bridge are fully installed."
