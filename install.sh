#!/usr/bin/env bash
# Linux/macOS launcher for the cross-platform Hermes Voicebox installer.
# Bootstraps missing Git/Python when possible, then runs install.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Normalise to a native filesystem path. On MSYS/Cygwin/ Git-for-Windows,
# BASH_SOURCE can resolve to a /c/... (MSYS) form that Python's Windows
# interpreter mis-reads as C:\c\... . Convert it so `python3 install.py`
# reliably finds the script regardless of environment.
if command -v cygpath >/dev/null 2>&1; then
  SCRIPT_DIR="$(cygpath -w "$SCRIPT_DIR" 2>/dev/null || echo "$SCRIPT_DIR")"
  SCRIPT_DIR="${SCRIPT_DIR//\\//}"   # Python accepts forward slashes on Windows
fi
cd "$SCRIPT_DIR"

# Guard: this script must live next to install.py. If it doesn't, the user
# almost certainly ran it from the wrong directory (the classic
# "./install.sh: No such file or directory" confusion). Say so clearly.
if [[ ! -f "$SCRIPT_DIR/install.py" ]]; then
  echo "ERROR: install.py not found next to this script." >&2
  echo "       This means you're not inside the cloned repo directory." >&2
  echo "       Run these first:" >&2
  echo "         git clone https://github.com/chuckatbcs/hermes-voicebox.git" >&2
  echo "         cd hermes-voicebox" >&2
  echo "       then:  python3 install.py --one-click" >&2
  echo "       (or:  bash install.sh)" >&2
  exit 1
fi

have_git() { command -v git >/dev/null 2>&1; }
have_python() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null
    return $?
  fi
  if command -v python >/dev/null 2>&1; then
    python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null
    return $?
  fi
  return 1
}

bootstrap_git() {
  echo "Git not found — attempting to install..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y git
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y git
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --noconfirm git
  else
    echo "ERROR: Install git manually, then re-run ./install.sh" >&2
    exit 1
  fi
}

bootstrap_python() {
  echo "Python 3.10+ not found — attempting to install..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-venv curl git xz-utils
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 python3-pip curl git
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --noconfirm python3 curl git
  else
    echo "ERROR: Install Python 3.10+ manually, then re-run ./install.sh" >&2
    exit 1
  fi
}

if ! have_git; then
  bootstrap_git
fi
if ! have_python; then
  bootstrap_python
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  PYTHON=python
fi

# Default to a fully non-interactive, fault-tolerant install when no TTY is
# attached OR when invoked without explicit flags. This makes the documented
# one-line install work whether the user runs `./install.sh`,
# `./install.sh --one-click`, or `bash install.sh` from a script/CI.
ARGS=("$@")
if [[ ! -t 0 ]] || [[ $# -eq 0 ]]; then
  # Auto-append --one-click (implies -y, best-effort) unless the user already
  # passed a yes/one-click flag.
  if [[ ! " ${ARGS[*]-} " =~ " -y " ]] && \
     [[ ! " ${ARGS[*]-} " =~ " --yes " ]] && \
     [[ ! " ${ARGS[*]-} " =~ " --one-click " ]]; then
    ARGS+=("--one-click")
  fi
elif [[ " ${ARGS[*]-} " =~ " --one-click " ]]; then
  # Honor explicit --one-click (it implies -y internally in install.py).
  :
fi

exec "$PYTHON" "$SCRIPT_DIR/install.py" "${ARGS[@]}"
