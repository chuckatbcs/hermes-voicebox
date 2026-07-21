#!/bin/bash
# Linux installer for Hermes Voicebox integration
set -e

HERMES_DIR="$HOME/.hermes"
echo "=== Installing Hermes Voicebox Integration (Linux) ==="

# 1. Ensure target directories exist
mkdir -p "$HERMES_DIR/desktop-plugins/voice-switcher"
mkdir -p "$HERMES_DIR/scripts"

# 2. Copy plugin files
echo "Copying plugin UI..."
cp desktop-plugin/plugin.js "$HERMES_DIR/desktop-plugins/voice-switcher/plugin.js"

# 3. Copy python bridge script
echo "Copying python bridge..."
cp scripts/voicebox_tts.py "$HERMES_DIR/scripts/voicebox_tts.py"
chmod +x "$HERMES_DIR/scripts/voicebox_tts.py"

# 4. Prompt user to configure hermes config.yaml
echo ""
echo "=== Setup Complete ==="
echo "Please add/replace the following section in your $HERMES_DIR/config.yaml:"
echo ""
echo "tts:"
echo "  provider: voicebox"
echo "  providers:"
echo "    voicebox:"
echo "      type: command"
echo "      command: python3 \$HOME/.hermes/scripts/voicebox_tts.py --text-file {input_path} --out {output_path} --voice {voice}"
echo "      voice: default"
echo "      output_format: wav"
echo ""
echo "Make sure your Voicebox API is running locally on port 17493!"
