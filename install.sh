#!/usr/bin/env bash
# Linux/macOS launcher for the cross-platform Hermes Voicebox installer.
# Bootstraps missing Git/Python when possible, clones/updates the repo
# (self-healing a partial clone), then runs install.py.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Normalise to a native filesystem path. On MSYS/Cygwin/ Git-for-Windows,
# BASH_SOURCE can resolve to a /c/... (MSYS) form that Python's Windows
# interpreter mis-reads as C:\c\... . Convert it so `python3 install.py`
# reliably finds the script regardless of environment.
if command -v cygpath >/dev/null 2>&1; then
  SCRIPT_DIR="$(cygpath -w "$SCRIPT_DIR" 2>/dev/null || echo "$SCRIPT_DIR")"
  SCRIPT_DIR="${SCRIPT_DIR//\\//}"   # Python accepts forward slashes on Windows
fi

# If this script is NOT sitting next to install.py, we're being run from some
# other directory (e.g. the README's "git clone ... && cd hermes-voicebox &&
# python3 install.py" was truncated, or the user just ran install.sh from ~).
# Self-heal: clone/update the repo into ./hermes-voicebox and re-exec inside it.
if [[ ! -f "$SCRIPT_DIR/install.py" ]]; then
  # If there's a copy of install.sh already inside ./hermes-voicebox, prefer it.
  if [[ -f "hermes-voicebox/install.sh" ]]; then
    exec bash "hermes-voicebox/install.sh" "$@"
  fi
  echo "install.py not beside this script — cloning/updating the repo first..."
  REPO_URL="https://github.com/chuckatbcs/hermes-voicebox.git"
  REPO_DIR="hermes-voicebox"
  if [[ -d "$REPO_DIR/.git" ]]; then
    echo "Repo present at ./$REPO_DIR — updating (git pull)..."
    git -C "$REPO_DIR" pull --ff-only 2>&1 | tail -3 || {
      echo "WARNING: git pull failed; repairing clone..."
      bak="${REPO_DIR}.bak.$$"; mv "$REPO_DIR" "$bak" 2>/dev/null
      git clone "$REPO_URL" "$REPO_DIR" 2>&1 | tail -3
    }
  elif [[ -e "$REPO_DIR" ]]; then
    echo "Found a non-git ./$REPO_DIR (partial clone) — repairing..."
    bak="${REPO_DIR}.bak.$$"; mv "$REPO_DIR" "$bak" 2>/dev/null
    git clone "$REPO_URL" "$REPO_DIR" 2>&1 | tail -3
  else
    git clone "$REPO_URL" "$REPO_DIR" 2>&1 | tail -3
  fi
  exec bash "hermes-voicebox/install.sh" "$@"
fi

cd "$SCRIPT_DIR"

# Guard (defensive — we should now be inside the repo): install.py must exist.
if [[ ! -f "$SCRIPT_DIR/install.py" ]]; then
  echo "ERROR: install.py still not found after clone." >&2
  echo "       Remove ./hermes-voicebox and re-run." >&2
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

# Clone the repo, or update it if it already exists, or repair it if a previous
# clone was interrupted (the classic "destination path already exists and is not
# an empty directory" / partial-clone trap). Idempotent and re-runnable.
REPO_URL="https://github.com/chuckatbcs/hermes-voicebox.git"
REPO_DIR="${REPO_DIR:-hermes-voicebox}"

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
