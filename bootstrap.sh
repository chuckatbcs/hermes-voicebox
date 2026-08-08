#!/usr/bin/env bash
# Bootstrap installer for Hermes Voicebox (Linux/macOS).
#
# Fetches the latest install.sh from GitHub and runs it. install.sh then
# clones/updates the repo (self-healing a partial clone) and runs the
# idempotent installer. This avoids the "git clone ... already exists" trap
# entirely — re-running this always repairs or updates.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/chuckatbcs/hermes-voicebox/master/bootstrap.sh | bash
#
# Or, to pass installer flags through:
#   curl -fsSL https://raw.githubusercontent.com/chuckatbcs/hermes-voicebox/master/bootstrap.sh | bash -s -- --one-click
set -uo pipefail

INSTALL_SH_URL="https://raw.githubusercontent.com/chuckatbcs/hermes-voicebox/master/install.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Fetching installer..."
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$INSTALL_SH_URL" -o "$TMP/install.sh" || {
    echo "ERROR: failed to fetch install.sh (network?)" >&2; exit 1; }
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$TMP/install.sh" "$INSTALL_SH_URL" || {
    echo "ERROR: failed to fetch install.sh (network?)" >&2; exit 1; }
else
  echo "ERROR: need curl or wget to bootstrap." >&2; exit 1
fi

chmod +x "$TMP/install.sh"
exec bash "$TMP/install.sh" "$@"
