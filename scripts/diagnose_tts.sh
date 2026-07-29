#!/usr/bin/env bash
# Trace Voicebox TTS health for Hermes Voicebox integration.
# Usage: bash scripts/diagnose_tts.sh [profile-id]
set -euo pipefail

PROFILE_ID="${1:-c7bb4b3a-22ee-4013-b0a5-d77792877af4}"
BASE_URL="${VOICEBOX_URL:-http://127.0.0.1:17493}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_DIR="${HERMES_DIR:-$HOME/.hermes}"
TRACE_LOG="${TMPDIR:-/tmp}/hermes-voicebox-diagnose-$(date +%Y%m%d-%H%M%S).log"

exec > >(tee -a "$TRACE_LOG") 2>&1

step() { printf '\n======== %s ========\n' "$*"; }

step "Trace start"
echo "host=$(hostname) user=$(whoami) date=$(date -Is)"
echo "repo=$REPO_DIR"
echo "hermes=$HERMES_DIR"
echo "base_url=$BASE_URL"
echo "profile_id=$PROFILE_ID"
echo "log=$TRACE_LOG"

step "1) Repo branch / HEAD"
cd "$REPO_DIR"
git remote -v || true
git fetch origin 2>&1 || true
git status -sb || true
git branch --show-current || true
git rev-parse HEAD || true
git log -1 --oneline || true

step "2) Checkout PR branch + pull latest"
if git show-ref --verify --quiet refs/remotes/origin/cursor/plugin-review-fixes-c044; then
  git checkout cursor/plugin-review-fixes-c044 2>&1 || git checkout -b cursor/plugin-review-fixes-c044 origin/cursor/plugin-review-fixes-c044
  git pull --ff-only origin cursor/plugin-review-fixes-c044 2>&1 || git pull origin cursor/plugin-review-fixes-c044 2>&1 || true
else
  echo "WARN: origin/cursor/plugin-review-fixes-c044 not found; staying on current branch"
fi
echo "now HEAD=$(git rev-parse HEAD)"
echo "now branch=$(git branch --show-current)"

step "3) Reinstall plugin + bridge (skip prereqs)"
if [[ -f install.py ]]; then
  python3 install.py -y --skip-prereqs 2>&1 || bash install.sh -y --skip-prereqs 2>&1 || true
else
  bash install.sh -y --skip-prereqs 2>&1 || true
fi
ls -la "$HERMES_DIR/desktop-plugins/voice-switcher/plugin.js" 2>&1 || true
ls -la "$HERMES_DIR/scripts/voicebox_tts.py" 2>&1 || true

step "4) Voicebox /health"
HEALTH_JSON=$(curl -sS -m 10 "$BASE_URL/health" || echo '{"status":"HEALTH_FAILED"}')
echo "$HEALTH_JSON"
if command -v jq >/dev/null 2>&1; then
  echo "gpu_available=$(echo "$HEALTH_JSON" | jq -r '.gpu_available // "unknown"')"
  echo "backend_variant=$(echo "$HEALTH_JSON" | jq -r '.backend_variant // "unknown"')"
fi

step "4b) Host GPU / CUDA sanity (common cause of /generate/stream HTTP 500)"
if command -v nvidia-smi >/dev/null 2>&1; then
  # Capture without hanging forever if the driver is wedged
  if NVIDIA_OUT=$(timeout 8 nvidia-smi 2>&1); then
    echo "$NVIDIA_OUT" | head -20
    if echo "$NVIDIA_OUT" | grep -q 'ERR!'; then
      echo "DIAG: nvidia-smi reports ERR! — GPU driver is wedged (often after suspend)."
      echo "DIAG: restart Voicebox on CPU (CUDA_VISIBLE_DEVICES=) or reboot to clear CUDA."
    fi
  else
    echo "DIAG: nvidia-smi hung or failed (exit $?) — treat GPU as wedged."
    echo "DIAG: systemctl --user restart voicebox.service after setting CUDA_VISIBLE_DEVICES="
  fi
else
  echo "nvidia-smi not present (OK on CPU-only hosts)"
fi

step "5) Profile detail"
if command -v jq >/dev/null 2>&1; then
  curl -sS -m 10 "$BASE_URL/profiles/$PROFILE_ID" | jq . || echo "PROFILE_FETCH_FAILED"
else
  curl -sS -m 10 "$BASE_URL/profiles/$PROFILE_ID" || echo "PROFILE_FETCH_FAILED"
fi

step "6) Models status"
if command -v jq >/dev/null 2>&1; then
  curl -sS -m 30 "$BASE_URL/models/status" | jq . || echo "MODELS_FAILED"
else
  curl -sS -m 30 "$BASE_URL/models/status" || echo "MODELS_FAILED"
fi

step "7) List profiles (name/id/engine/type/samples)"
if command -v jq >/dev/null 2>&1; then
  curl -sS -m 10 "$BASE_URL/profiles" | jq -r '.[] | "\(.name)\t\(.id)\t\(.voice_type)\t\(.default_engine // .preset_engine)\tpreset=\(.preset_voice_id // "-")\tsamples=\(.sample_count // 0)"' || true
else
  curl -sS -m 10 "$BASE_URL/profiles" | head -c 4000 || true
fi

step "8) Direct /generate/stream smoke test"
HTTP_CODE=$(curl -sS -m 120 -o /tmp/vb-diagnose-test.wav -w '%{http_code}' \
  -X POST "$BASE_URL/generate/stream" \
  -H 'Content-Type: application/json' \
  -d "{\"profile_id\":\"$PROFILE_ID\",\"text\":\"Hello from Hermes Voicebox diagnose.\",\"language\":\"en\"}" \
  || echo "curl_error")
echo "http_code=$HTTP_CODE"
ls -la /tmp/vb-diagnose-test.wav 2>&1 || true
file /tmp/vb-diagnose-test.wav 2>&1 || true
# If not WAV, show body snippet (often JSON error)
if ! file /tmp/vb-diagnose-test.wav 2>/dev/null | grep -qi 'wave\|audio'; then
  echo "Non-audio response body (first 800 bytes):"
  head -c 800 /tmp/vb-diagnose-test.wav 2>/dev/null || true
  echo
fi

step "9) Bridge dry-run (if python bridge present)"
BRIDGE="$HERMES_DIR/scripts/voicebox_tts.py"
if [[ -f "$BRIDGE" ]]; then
  TEXT_FILE=$(mktemp)
  OUT_FILE=$(mktemp --suffix=.wav)
  echo "Hello from bridge diagnose." >"$TEXT_FILE"
  set +e
  python3 "$BRIDGE" --text-file "$TEXT_FILE" --out "$OUT_FILE" --voice "$PROFILE_ID" --base-url "$BASE_URL"
  BRIDGE_RC=$?
  set -e
  echo "bridge_exit=$BRIDGE_RC"
  ls -la "$OUT_FILE" 2>&1 || true
  file "$OUT_FILE" 2>&1 || true
  rm -f "$TEXT_FILE"
else
  echo "Bridge not installed at $BRIDGE"
fi

step "10) MCP voicebox health"
CONFIG_YAML="${HERMES_HOME:-$HERMES_DIR}/config.yaml"
if [[ -f "$CONFIG_YAML" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$CONFIG_YAML" <<'PY' || true
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    print("DIAG: PyYAML missing; skip mcp_servers parse")
    raise SystemExit(0)
cfg = yaml.safe_load(Path(sys.argv[1]).read_text()) or {}
mcp = (cfg.get("mcp_servers") or {})
vb = mcp.get("voicebox") if isinstance(mcp, dict) else None
print(f"mcp_servers.voicebox present={bool(vb)}")
if isinstance(vb, dict):
    print(f"  enabled={vb.get('enabled')}")
    print(f"  command={vb.get('command')}")
    print(f"  args={vb.get('args')}")
PY
  fi
else
  echo "DIAG: no config.yaml at $CONFIG_YAML"
fi
if command -v pgrep >/dev/null 2>&1; then
  echo "mcp_shim processes:"
  pgrep -af 'backend.mcp_shim|mcp_stdio_watchdog' || echo "  (none)"
fi
if command -v hermes >/dev/null 2>&1; then
  set +e
  hermes mcp list 2>&1 | head -30 || true
  hermes mcp test voicebox 2>&1 | head -40 || true
  set -e
else
  echo "DIAG: hermes CLI not on PATH — skip mcp test"
fi

step "Done"
echo "Full trace saved to: $TRACE_LOG"
echo "Paste this entire terminal output (or the log file) back to the agent."
