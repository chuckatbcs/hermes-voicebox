# Hermes Voicebox Integration

Cross-platform integration between the **Hermes Desktop Client** and **Voicebox API**: a desktop plugin for voice profiles (Select/Upload/Delete) plus a Python TTS bridge.

## Features

- **Smart Engine Routing** for Kokoro / Chatterbox / Qwen profiles (deprecated `qwen_fast` / `qwen3` remapped to `qwen`)
- **Laptop-safe Qwen default** — bridge sends `model_size=0.6B` unless `VOICEBOX_QWEN_MODEL_SIZE` overrides (avoids CUDA OOM on ~8 GiB GPUs)
- **CUDA OOM recovery** — on out-of-memory, unload Voicebox models, force 0.6B, retry once; hints for Free GPU / service restart when unload is a no-op
- **Stage-direction sanitization** — strips `[acting notes]`, theatrical `(parens)`, and `*emphasis*` so Qwen does not speak markup aloud (keeps Chatterbox tags like `[laugh]`)
- **Sentence chunking & WAV merging** for long AI responses (clone engines speak one sentence per request so Chatterbox early-stops don't truncate the rest)
- **Desktop speak-stream hook** so read-aloud starts on the first sentence and look-ahead-synthesizes the next while the current one plays (plus a 600s command TTS timeout)
- **Per-profile voice + persona binding** — Desktop selection writes profile-scoped config + `$HERMES_HOME/voicebox_binding.json` (does not clear `display.personality` or prefer Voicebox’s process-global active voice)
- **GPU lifecycle** — Free GPU button, idle unload (~15 min after last TTS), and optional Voicebox stop when Hermes Desktop quits
- **In-plugin microphone recording** (plus native OS file picker) for voice cloning samples
- **Demo clone voices** (Eric Cartman, Sexy Girl, Amanda, Vincent Price, Jarvis) seeded from reference WAVs in `samples/` so they sound identical across every install (Windows + Linux) — no copyrighted audio bundled. Persona text is applied per voice.
- **Safe two-step voice deletion**
- **Installer that checks prerequisites and provisions what’s missing** (Hermes, Voicebox, TTS models)
- **Diagnose script** — TTS + GPU + MCP `voicebox` health (`scripts/diagnose_tts.sh`, also installed under `~/.hermes/scripts/`)

---

## What the installer provisions

| Prerequisite | How it’s handled |
|--------------|------------------|
| **Python 3.10+** | Detected; installed via `winget` (Windows) or `apt`/`dnf` (Linux) if missing |
| **Git** | Installed when needed to clone Voicebox for Docker |
| **Hermes Agent / Desktop** | Official installer (`install.sh` / `install.ps1` from Nous) if not detected |
| **Voicebox API (:17493)** | Reuses a running API, starts systemd/docker if present; otherwise provisions via **Docker Compose** (Linux/Windows) or the **Windows desktop installer** |
| **TTS models** | Queries `/models/status`, downloads missing engines via `/models/download` (default profile: Kokoro + Qwen + Chatterbox + Turbo) |
| **This plugin + bridge** | Copied into `~/.hermes` / `%USERPROFILE%\.hermes` with absolute-path TTS config |
| **Demo clone voices** | Seeds the canonical demo **clone** voices (reference WAVs in `samples/`) into Voicebox via `/profiles` — identical on Windows and Linux. Skip with `--skip-samples` |

The installer does **not** invent a private Voicebox fork — it uses upstream [jamiepine/voicebox](https://github.com/jamiepine/voicebox) and the public Voicebox model APIs.

---

## Quick install

### Linux

```bash
chmod +x install.sh
./install.sh -y
```

### Windows (PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Yes
```

`-y` / `-Yes` auto-approves downloads and package installs (model downloads can be multiple GB).

Then **restart Hermes Desktop** and open the **Voicebox** sidebar entry.

---

## Installer options

```bash
# Plugin files only (no Hermes/Voicebox/model provisioning)
./install.sh --skip-prereqs

# Ensure only the small Kokoro model
./install.sh -y --model-profile minimal

# Download every TTS model Voicebox reports (large!)
./install.sh -y --model-profile all

# Extra explicit model names
./install.sh -y --model qwen-tts-1.7B --model kokoro

# Skip pieces you already manage yourself
./install.sh -y --skip-hermes --skip-voicebox
./install.sh -y --skip-models

# Custom Hermes home / Voicebox URL
./install.sh -y --hermes-dir "$HOME/.hermes" --base-url http://127.0.0.1:17493
```

Windows equivalents use the same flags as `install.ps1` parameters (`-SkipPrereqs`, `-ModelProfile minimal`, `-PreferDocker`, `-PreferDesktop`, etc.).

You can also run the shared entrypoint directly:

```bash
python3 install.py -y
python install.py -y
```

### Model profiles

| Profile | Engines ensured |
|---------|-----------------|
| `minimal` | Kokoro |
| `plugin` (default) | Kokoro, Qwen, Chatterbox, Chatterbox Turbo |
| `all` | Every non-Whisper model from `/models/status` |

### Seeded demo voices (clones)

After models download, the installer seeds a fixed set of **demo clone voices** from `samples/manifest.json` + the reference WAVs in `samples/`. These are identical on **Windows and Linux** so a clone sounds the same on every machine. The installer is idempotent — it skips any **cloned** voice whose name already exists (presets of the same name do not block seeding). No Kokoro preset personas are seeded; demo voices are real clones only.

| Demo voice | Engine | Notes |
|------------|--------|-------|
| Eric Cartman | qwen | Parody comic persona |
| Sexy Girl | chatterbox_turbo | |
| Amanda | chatterbox_turbo | |
| Vincent Price | chatterbox_turbo | Gothic horror-host persona |
| Jarvis | chatterbox_turbo | British butler-AI persona |

**Recommended clone engine: `chatterbox_turbo`.** It is the fastest Chatterbox variant, speaks one sentence per request (so long replies don't truncate via early-stop), and is included in the default `plugin` model profile. Use `qwen` (0.6B default, 1.7B optional) when you want a different timbre. Reference WAVs are user-authored demo recordings — no copyrighted audio is bundled.

To pull these voices into a fresh Voicebox without a full reinstall: `python install.py -y --skip-prereqs --skip-models --skip-samples` is *not* what you want — instead just re-run the installer normally (it will skip existing profiles and only seed missing ones). Disable seeding with `--skip-samples`.

---

## Configuration

- **`VOICEBOX_PORT`** / **`--base-url`**: TTS bridge backend URL (default `http://127.0.0.1:17493`)
- **`HERMES_HOME`** / **`HERMES_DIR`**: Hermes profile home (default `~/.hermes`). Named Desktop profiles use `~/.hermes/profiles/<name>/`.
- **Plugin prefs (`ctx.storage`)**: under `hermes.plugin.voice-switcher.*` (active voice per profile, dismissed samples, backend URL override, stop-on-exit). Legacy bare `localStorage` keys are migrated once on load.
- **Active voice (per Hermes profile)**: selecting a voice in the plugin binds it to the **current** Hermes Desktop profile (`host.state.profile`):
  - writes `tts.providers.voicebox.voice` via Desktop `PUT /api/config` (profile-scoped)
  - applies persona via gateway `config.set personality` (already profile-scoped)
  - caches UI selection in plugin storage keyed by profile (`active_voice:<profile>`)
  - optional sidecar `$HERMES_HOME/voicebox_binding.json` for CLI/non-Desktop agents
- **Enable / disable**: Settings → Plugins → **Voicebox Integration** (ships `defaultEnabled: false` — turn it on once after install/upgrade)
- **Not used as source of truth**: Voicebox `/settings/active-voice` is process-global and would bleed across Hermes profiles — the plugin no longer prefers it.

Bridge voice precedence: CLI `--voice` → `$HERMES_HOME/voicebox_binding.json` (or legacy `voicebox_active_voice.json`) → Voicebox active-voice (demoted) → first Voicebox profile.

### TTS bridge behavior

| Concern | Behavior |
|---------|----------|
| Engine ids | `qwen_fast` / `qwen3` → `qwen` before `/generate` |
| Qwen VRAM | Default `model_size=0.6B`; override with `VOICEBOX_QWEN_MODEL_SIZE=1.7B` (or `0.6B`) |
| Spoken text | Strips stage directions / emphasis markup; keeps Chatterbox paralinguistic tags |
| CUDA OOM | Unload via `/models/*/unload` + `/models/unload`, force 0.6B, retry once |
| Stuck VRAM | If unload does not free memory, `systemctl --user restart voicebox.service` (or plugin Free GPU then restart) |
| Activity stamp | Updates `$HERMES_HOME/voicebox_tts_activity` so idle GPU unload does not race mid-speak |

CLI bind helper (for gateway / other apps):

```bash
HERMES_HOME=~/.hermes/profiles/work python3 ~/.hermes/scripts/voicebox_bind.py \
  --voice <voicebox-profile-uuid> --persona-key jarvis
```

Installer copies the desktop plugin + bridge into each profile home (Desktop loads
plugins from that profile’s `HERMES_HOME/desktop-plugins/`) and merges the TTS block:

```bash
./install.sh --skip-prereqs --all-profiles
# or one profile:
./install.sh --skip-prereqs --profile work
```

After creating a new Hermes Desktop profile, re-run `--all-profiles` (or `--profile <name>`)
and restart Desktop so the Voicebox sidebar appears on that profile.

Windows:

```powershell
.\install.ps1 -SkipPrereqs -AllProfiles
.\install.ps1 -SkipPrereqs -Profile work
```

Bridge URL precedence: `--base-url` → `VOICEBOX_PORT` → default `17493`.

The installer writes a marked block into each target profile’s `config.yaml`:

```yaml
# BEGIN hermes-voicebox
tts:
  provider: voicebox
  providers:
    voicebox:
      type: command
      command: python3 /absolute/path/to/voicebox_tts.py --text-file {input_path} --out {output_path} --voice {voice}
      voice: default
      output_format: wav
      timeout: 600
# END hermes-voicebox
```

On Windows the command uses `python` or `py -3` plus your absolute bridge path.

### Hermes Agent patches (speak-stream)

When `$HERMES_HOME/hermes-agent` is present, the installer also:

| Target | Marker | Purpose |
|--------|--------|---------|
| `hermes-agent/tools/voicebox_command_streamer.py` | (copied from `scripts/hermes_voicebox_streamer.py`) | Sentence PCM streamer with look-ahead synth |
| `hermes-agent/tools/tts_streaming.py` | `# BEGIN hermes-voicebox-streamer` | Import/register the Voicebox streamer |
| `hermes-agent/hermes_cli/web_server.py` | `# BEGIN hermes-voicebox-prefetch` | Prefetch next sentence while current audio plays |

First patch creates sibling `*.voicebox.bak` backups. **Hermes updates overwrite these files** — re-run after upgrading Hermes:

```bash
python3 install.py -y --skip-prereqs --skip-hermes
# Windows:
.\install.ps1 -Yes -SkipPrereqs -SkipHermes
```

If `hermes-agent` is missing, plugin + command TTS still work; Desktop read-aloud stays one-shot until the hook is installed.

### GPU / VRAM lifecycle

Voicebox keeps TTS models in VRAM after first speak. This package adds three release paths:

| Path | How |
|------|-----|
| **Manual** | Plugin **Free GPU** button, or `python3 ~/.hermes/scripts/voicebox_gpu.py unload` |
| **Idle** | `voicebox-gpu-lifecycle` user service unloads models ~15 minutes after last TTS (stamp file updated by the bridge) |
| **Hermes exit** | Same service stops Voicebox after Hermes Desktop has been gone ~20s (toggle in plugin; default on) |

Linux installer enables `~/.config/systemd/user/voicebox-gpu-lifecycle.service` (localhost control API on `127.0.0.1:17494`). Skip with `--skip-gpu-lifecycle`. Keep Voicebox running after Hermes quits with `--no-stop-voicebox-on-hermes-exit`.

```bash
python3 ~/.hermes/scripts/voicebox_gpu.py status
python3 ~/.hermes/scripts/voicebox_gpu.py unload
python3 ~/.hermes/scripts/voicebox_gpu.py stop    # unload + systemctl/docker stop
python3 ~/.hermes/scripts/voicebox_gpu.py config --stop-on-hermes-exit off
```

Windows: use the plugin button / CLI; the systemd unit is Linux-only (you can still run `lifecycle-daemon` manually if desired).

**OOM recovery:** the TTS bridge (`voicebox_tts.py`) detects CUDA out-of-memory, calls Voicebox `/models/unload`, forces Qwen `model_size=0.6B`, and retries once. If unload reports success but VRAM barely drops, restart Voicebox (`systemctl --user restart voicebox.service`) — the unload API can be a no-op while the process still holds memory.

**Crash-loop prevention:** the `voicebox-gpu-lifecycle` daemon now checks Voicebox `/health` before attempting unload — if Voicebox is unreachable, it skips the unload call and enters exponential backoff (up to 5 minutes) instead of hammering the dead process every 10 seconds. It also verifies Voicebox actually started after calling `start_voicebox()`, so a broken Voicebox won't trigger an infinite restart loop. If `nvidia-smi` hangs or won't respond after a crash, a system reboot is required to clear the wedged CUDA context.

### Diagnose script

```bash
# From the repo:
bash scripts/diagnose_tts.sh [voice-profile-uuid]
# After install:
bash ~/.hermes/scripts/diagnose_tts.sh [voice-profile-uuid]
```

Checks Voicebox `/health`, GPU/`nvidia-smi`, profile metadata, a direct `/generate/stream` smoke test, the installed bridge, and **MCP `voicebox`** (`hermes mcp list` / `hermes mcp test voicebox` + shim process presence).

Optional MCP (Hermes `mcp_servers.voicebox` → Voicebox `backend.mcp_shim`) is separate from command TTS: the bridge always talks HTTP to `:17493`; MCP exposes `voicebox.speak` / `transcribe` / `list_*` tools when configured and connected.

---

## Manual Voicebox notes

- **Windows**: desktop installer from https://voicebox.sh/download/windows (used automatically unless `--prefer-docker`)
- **Linux**: no official desktop binary yet — installer uses **Docker Compose** (`~/.hermes/vendor/voicebox`). See https://docs.voicebox.sh/overview/docker
- Models are cached in the HuggingFace cache (or the Docker `huggingface-cache` volume)

---

## Development / validation

```bash
python3 -m py_compile install.py installer/prereqs.py \
  scripts/voicebox_tts.py scripts/voicebox_bind.py scripts/voicebox_gpu.py \
  scripts/hermes_voicebox_streamer.py
python3 -m unittest test_install.py -v
(cd scripts && python3 -m unittest test_voicebox_tts.py test_voicebox_gpu.py -v)
./install.sh --hermes-dir /tmp/hermes-test --skip-prereqs
```

### Windows from-scratch smoke test

Simulates a new user’s Windows launcher against a clean Hermes directory (skips live Hermes/Voicebox/model downloads in CI):

```powershell
pwsh -NoProfile -File .\test_windows_scratch.ps1
# or on Windows:
powershell -NoProfile -ExecutionPolicy Bypass -File .\test_windows_scratch.ps1
```

On a real Windows PC, a brand-new user typically runs:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Yes
```

Then restart Hermes Desktop.
