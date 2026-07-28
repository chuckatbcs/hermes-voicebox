#!/usr/bin/env bash
# Linux/macOS launcher for the cross-platform Hermes Voicebox installer.
# Bootstraps missing Python/Git when possible, then runs install.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

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

bootstrap_python() {
  echo "Python 3.10+ not found — attempting to install..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-venv curl git xz-utils
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 python3-pip curl git
  else
    echo "ERROR: Install Python 3.10+ manually, then re-run ./install.sh" >&2
    exit 1
  fi
}

if ! have_python; then
  bootstrap_python
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  PYTHON=python
fi

# Default to non-interactive friendly flags if stdout isn't a TTY and -y not already passed.
ARGS=("$@")
if [[ ! -t 0 ]] && [[ ! " ${ARGS[*]-} " =~ " -y " ]] && [[ ! " ${ARGS[*]-} " =~ " --yes " ]]; then
  echo "Non-interactive shell detected; pass -y explicitly for auto-provisioning."
fi

exec "$PYTHON" "$SCRIPT_DIR/install.py" "${ARGS[@]}"
