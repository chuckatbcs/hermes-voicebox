# Hermes Voicebox Integration

Cross-platform integration between the **Hermes Desktop Client** and **Voicebox API**: a desktop plugin for voice profiles (Select/Upload/Delete) plus a Python TTS bridge.

## Features

- **Smart Engine Routing** for Kokoro / Chatterbox / Qwen profiles
- **Sentence chunking & WAV merging** for long AI responses (clone engines speak one sentence per request so Chatterbox early-stops don't truncate the rest)
- **Desktop speak-stream hook** so read-aloud starts on the first sentence and look-ahead-synthesizes the next while the current one plays (plus a 600s command TTS timeout)
- **In-plugin microphone recording** (plus native OS file picker) for voice cloning samples
- **Fun sample persona voices** (Vincent Price, Porky Pig, Cartman, Jarvis, GLaDOS) seeded as parody templates with Hermes `/personality` keys — not official voice clones / no copyrighted audio bundled
- **Safe two-step voice deletion**
- **Installer that checks prerequisites and provisions what’s missing** (Hermes, Voicebox, TTS models)

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

---

## Configuration

- **`VOICEBOX_PORT`** / **`--base-url`**: TTS bridge backend URL (default `http://127.0.0.1:17493`)
- **`HERMES_HOME`** / **`HERMES_DIR`**: Hermes profile home (default `~/.hermes`). Named Desktop profiles use `~/.hermes/profiles/<name>/`.
- **`localStorage.voicebox_backend_url`**: optional plugin UI override
- **Active voice (per Hermes profile)**: selecting a voice in the plugin binds it to the **current** Hermes Desktop profile (`host.state.profile`):
  - writes `tts.providers.voicebox.voice` via Desktop `PUT /api/config` (profile-scoped)
  - applies persona via gateway `config.set personality` (already profile-scoped)
  - caches UI selection in `localStorage` keyed by profile (`voicebox_active_voice_id:<profile>`)
  - optional sidecar `$HERMES_HOME/voicebox_binding.json` for CLI/non-Desktop agents
- **Not used as source of truth**: Voicebox `/settings/active-voice` is process-global and would bleed across Hermes profiles — the plugin no longer prefers it.

Bridge voice precedence: CLI `--voice` → `$HERMES_HOME/voicebox_binding.json` (or legacy `voicebox_active_voice.json`) → Voicebox active-voice (demoted) → first Voicebox profile.

CLI bind helper (for gateway / other apps):

```bash
HERMES_HOME=~/.hermes/profiles/work python3 ~/.hermes/scripts/voicebox_bind.py \
  --voice <voicebox-profile-uuid> --persona-key jarvis
```

Installer can merge the TTS block into every profile home:

```bash
./install.sh --skip-prereqs --all-profiles
# or one profile:
./install.sh --skip-prereqs --profile work
```

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

---

## Manual Voicebox notes

- **Windows**: desktop installer from https://voicebox.sh/download/windows (used automatically unless `--prefer-docker`)
- **Linux**: no official desktop binary yet — installer uses **Docker Compose** (`~/.hermes/vendor/voicebox`). See https://docs.voicebox.sh/overview/docker
- Models are cached in the HuggingFace cache (or the Docker `huggingface-cache` volume)

---

## Development / validation

```bash
python3 -m py_compile install.py installer/prereqs.py \
  scripts/voicebox_tts.py scripts/voicebox_bind.py scripts/hermes_voicebox_streamer.py
python3 -m unittest test_install.py -v
(cd scripts && python3 -m unittest test_voicebox_tts.py -v)
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
