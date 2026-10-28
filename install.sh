#!/bin/bash
# Linux/macOS launcher for the Hermes Voicebox integration installer.
#
# This is a thin wrapper around install.py, which performs the FULL install:
#   1. Prerequisite check & provisioning (Python deps, Hermes, Voicebox,
#      TTS models, demo voices)
#   2. Desktop plugin + TTS bridge + Fish provider scripts -> $HERMES_DIR
#   3. Marked TTS block merged into every profile's config.yaml
#   4. Speak-stream hook patches into hermes-agent (chunked read-aloud)
#   5. GPU lifecycle daemon (systemd user unit) + backend server on :17493
#
# All command-line flags are passed through to install.py:
#   ./install.sh                          # interactive, provisions everything
#   ./install.sh -y                       # non-interactive
#   ./install.sh --one-click              # best-effort, tolerates partial failures
#   ./install.sh --skip-prereqs           # plugin/bridge/config files only
#   ./install.sh --all-profiles -y        # install into every Hermes profile
#
# See: python3 install.py --help
set -e

cd "$(dirname "$0")"

PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "Python 3 not found — installing it for you..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -qq && sudo apt-get install -y python3 python3-pip python3-venv
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y python3 python3-pip
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm python
    elif command -v zypper >/dev/null 2>&1; then
        sudo zypper install -y python3 python3-pip
    else
        echo "ERROR: no supported package manager found." >&2
        echo "Install Python 3.10+ from https://python.org and re-run." >&2
        exit 1
    fi
    command -v python3 >/dev/null 2>&1 || PY=python
    if ! command -v "$PY" >/dev/null 2>&1; then
        echo "ERROR: Python install finished but python3 is still not on PATH." >&2
        echo "Open a new terminal and re-run this script." >&2
        exit 1
    fi
fi

echo "=== Installing Hermes Voicebox Integration & Backend ==="
exec "$PY" install.py "$@"
