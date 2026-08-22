#!/usr/bin/env bash
# Bootstrap installer for Hermes Voicebox (Linux/macOS).
#
# One-shot entry point:
#   curl -fsSL https://raw.githubusercontent.com/chuckatbcs/hermes-voicebox/master/bootstrap.sh | bash -s -- --one-click
#
# Clones/updates this repository into ~/hermes-voicebox (self-healing on
# re-runs), then runs install.py with any flags passed through.
#
# Python is provisioned automatically if missing (apt/dnf/pacman/zypper).
set -uo pipefail

REPO_URL="https://github.com/chuckatbcs/hermes-voicebox.git"
DEST="${HERMES_VOICEBOX_DIR:-$HOME/hermes-voicebox}"

# --- Ensure git -------------------------------------------------------------
if ! command -v git >/dev/null 2>&1; then
    echo "git not found — installing..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -qq && sudo apt-get install -y git
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y git
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm git
    elif command -v zypper >/dev/null 2>&1; then
        sudo zypper install -y git
    else
        echo "ERROR: no supported package manager found; install git and re-run." >&2
        exit 1
    fi
fi

# --- Clone or update the repo ------------------------------------------------
if [ -d "$DEST/.git" ]; then
    echo "Updating existing checkout at $DEST ..."
    git -C "$DEST" fetch origin master || true
    git -C "$DEST" reset --hard origin/master || {
        echo "ERROR: failed to update $DEST" >&2; exit 1; }
else
    echo "Cloning hermes-voicebox into $DEST ..."
    git clone --depth 1 "$REPO_URL" "$DEST" || {
        echo "ERROR: clone failed (network?)" >&2; exit 1; }
fi

cd "$DEST"

# --- Run the real installer ---------------------------------------------------
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
fi

echo "=== Running Hermes Voicebox installer ==="
exec bash install.sh "$@"
