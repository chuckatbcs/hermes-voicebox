# Installation Guide — Hermes Voicebox Integration

This guide gets you from zero to talking Hermes on **Linux or Windows**.
One command installs everything; then you restart Hermes Desktop, pick a
voice, and (optionally) add a free hosted-TTS key.

---

## 📋 What gets installed

The installer is idempotent — safe to re-run any time. It sets up **five
pieces**:

| # | Piece | What it is | Runs as |
|---|-------|-----------|---------|
| 1 | **Voicebox backend** (`voicebox_server.py`) | Local REST server on `http://127.0.0.1:17493` that runs the actual TTS models (Kokoro / Chatterbox / Qwen) | Background process, auto-started by the installer |
| 2 | **Desktop plugin** (`plugin.js`) | The "Voicebox Control" panel in the Hermes Desktop sidebar — voice picker, cloning UI, provider switch | Loaded by Hermes Desktop |
| 3 | **TTS bridge** (`voicebox_tts.py`) + **stream adapter** + **Fish provider** (`fish_tts.py`) | Command-line glue Hermes calls for every "speak" request; the stream adapter gives you sentence-by-sentence read-aloud | Invoked per-speak by Hermes |
| 4 | **GPU lifecycle daemon** (`voicebox_gpu.py`) | Unloads TTS models after ~10 min idle so other apps (Ollama, games) can use your VRAM; stops the backend when Hermes exits | systemd user unit (Linux) / Scheduled Task (Windows) |
| 5 | **Config wiring** | A marked block in each profile's `config.yaml` telling Hermes how to call the bridge, plus patches into `hermes-agent` that enable streaming read-aloud | Static config |

---

## ✅ Prerequisites (what you need before/what it gathers)

### Provided by the installer automatically
- Python dependencies (`fastapi`, `uvicorn`, `numpy`, `requests`, `PyYAML`)
- **Hermes Agent / Desktop** — detected; if missing, installed via the
  official script from `hermes-agent.nousresearch.com`
- **Voicebox engine** — cloned from upstream (`jamiepine/voicebox`) and run
  via Docker (Linux default) or the desktop app (Windows default)
- **TTS models** — downloaded through the Voicebox API:
  Kokoro 82M (~400 MB), Chatterbox Turbo (~4 GB VRAM), optional Qwen (~7 GB)

### You must have / provide yourself
| Requirement | Notes |
|---|---|
| **Python 3.10+** | Linux: `sudo apt install python3 python3-pip`. Windows: python.org installer ("Add to PATH"). The one-liner will tell you if it's missing. |
| **~6–10 GB free disk** | Models are multi-GB downloads. |
| **NVIDIA GPU (recommended)** | Any modern 8 GB+ card works great; CPU-only also works but synthesis is slow. No special CUDA setup needed — PyTorch inside the Voicebox environment handles it. |
| **Docker** (Linux, optional but recommended) | Only if you want the containerized Voicebox route instead of a local checkout. Get it from docker.com. |
| **Fish Audio API key** *(optional)* | Only for the hosted provider. Free tier at [fish.audio](https://fish.audio) → account settings → API keys. Enter it in the plugin UI after install — never in a terminal argument. |

No other accounts, keys, or services are required.

---

## 🚀 One-shot install

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/chuckatbcs/hermes-voicebox/master/bootstrap.sh | bash -s -- --one-click
```

or, from a cloned repo:

```bash
git clone https://github.com/chuckatbcs/hermes-voicebox.git
cd hermes-voicebox
./install.sh --one-click
```

### Windows (PowerShell)

```powershell
git clone https://github.com/chuckatbcs/hermes-voicebox.git
cd hermes-voicebox
.\install.ps1 --one-click
```

> `--one-click` = fully non-interactive, best-effort: it installs everything
> it can and keeps going if an optional piece (e.g. Docker) fails, reporting
> what needs attention at the end. Use plain `./install.sh` if you'd rather
> approve each provisioning step.

### Useful flags

| Flag | Effect |
|---|---|
| `-y` / `--yes` | Non-interactive (auto-approve downloads) |
| `--one-click` | `-y` + tolerate partial failures |
| `--skip-prereqs` | Files/config only; don't touch Python/Hermes/Voicebox/models |
| `--all-profiles` | Install into `$HERMES_HOME` **and every** `profiles/*` home |
| `--profile <name>` | Install into one specific profile home |
| `--skip-gpu-lifecycle` | Don't register the GPU daemon service |
| `--model-profile minimal` | Only download Kokoro (smallest footprint) |
| `--self-heal` | Diagnose + repair an existing/broken install |

Full list: `python3 install.py --help`

---

## 🎤 First-run setup (after install)

1. **Restart Hermes Desktop completely** (quit from the tray, reopen).
   This is *required* — the plugin and streaming hooks load at startup.
2. Open the sidebar → **Voicebox Control** panel.
3. Wait ~30 s on first connect while demo voices seed and models download.
4. Pick a preset persona (Jarvis, Cartman, Vincent Price…) or **clone your
   own**: record/upload a clean 10–30 s WAV of a voice you have rights to,
   name it, done.
5. Click 🔊 on any message to hear read-aloud.

### Optional: enable the hosted Fish Audio provider

1. Create a free account at [fish.audio](https://fish.audio) and copy your
   API key (Account Settings → API Keys).
2. In the **Voicebox Control** panel, paste the key into the
   **Fish Audio API Key** field. It's stored locally in
   `~/.hermes/config.yaml` and sent only to Fish's API during synthesis.
3. Flip the **TTS Provider** card to *Fish Audio (hosted)*.
4. Use the per-voice **🐟 Clone to Fish** button to mirror any cloned voice
   to Fish so it keeps working when your GPU is busy.

---

## 🔍 Verify the installation

```bash
# Backend healthy?
curl http://127.0.0.1:17493/health          # -> {"status":"healthy",...}

# Real audio end-to-end?
python3 ~/.hermes/scripts/voicebox_tts.py \
    --text "Installation test complete." \
    --out /tmp/vb-test.wav --voice default
python3 -c "import wave; w=wave.open('/tmp/vb-test.wav'); print('OK,', round(w.getnframes()/w.getframerate(),2), 'seconds')"

# Everything at once (28 checks):
python3 scripts/test_e2e_root_to_tip.py
```

Windows uses the same commands with `python` instead of `python3`.

---

## 🛠️ Troubleshooting

| Symptom | Fix |
|---|---|
| "Voicebox API NOT REACHABLE" | Start the backend manually: `python3 ~/.hermes/scripts/voicebox_server.py &` — or re-run the installer. |
| No sound / no speak-stream | Did you **restart Hermes Desktop** after installing? Also check `tts.provider` in `config.yaml`. |
| Wrong/default female voice on Fish | Your key isn't being found, or the clone label is missing. Re-enter the key in the plugin UI; verify with `cat ~/.hermes/fish_voices.json`. |
| Read-aloud waits for whole reply | The hermes-agent patches were overwritten by a Hermes update — re-run `./install.sh --skip-prereqs` and restart Desktop. |
| GPU busy / OOM with Ollama running | That's what the lifecycle daemon is for; see `systemctl --user status voicebox-gpu-lifecycle` (Linux). Or switch TTS provider to Fish temporarily. |
| Full diagnostics | `bash ~/.hermes/scripts/diagnose_tts.sh` |

---

## 🗑️ Uninstalling

```bash
systemctl --user stop voicebox.service voicebox-gpu-lifecycle.service   # Linux
rm -rf ~/.hermes/desktop-plugins/voice-switcher ~/.hermes/scripts/{voicebox_tts.py,fish_tts.py,voicebox_gpu.py}
```
Then remove the marked `# BEGIN hermes-voicebox … END hermes-voicebox`
block from `~/.hermes/config.yaml`.
