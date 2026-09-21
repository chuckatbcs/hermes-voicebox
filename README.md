# Hermes Voicebox Integration

A complete, end-to-end integration between the **Hermes Desktop Client** and **Voicebox API** — local-first voice cloning TTS with an optional hosted provider (Fish Audio). Includes a desktop plugin for managing voice profiles (select / clone / upload / delete), an intelligent Python execution bridge with sentence-level streaming, GPU lifecycle management, and an automated Voicebox REST backend.

Works on **Linux and Windows** from a single branch (parity is enforced by tests).

---

## 🚀 Features

- **Automated Backend & Server**: Built-in FastAPI Voicebox API server (`voicebox_server.py`) serving on `http://127.0.0.1:17493` with CORS support for the Hermes Desktop UI.
- **Voice Profile Management**: Profile listing, active-voice persistence, custom audio upload (`.wav`/`.mp3`), and safe profile deletion.
- **Smart Engine Routing**: Automatically maps resource usage based on voice profile engines:
  - 🟢 **Kokoro 82M** (~400 MB VRAM) for silent, ultra-fast daily TTS using presets.
  - 🟡 **Chatterbox 3B / Turbo** (~4 GB VRAM) for lightweight voice cloning.
  - 🔴 **Qwen TTS** (~7.6 GB VRAM; auto-downgrades to 0.6B on 8 GB GPUs) for highest-fidelity cloning.
- **Streaming read-aloud**: Desktop "read aloud" uses a look-ahead speak-stream pipeline — while sentence *N* plays, sentence *N+1* is already synthesizing. Applies to both the local Voicebox engine and the hosted Fish Audio provider.
- **Hosted fallback (Fish Audio)**: Optional per-profile hosted clone provider so TTS keeps working when the GPU is busy (e.g. Ollama resident). Voice clones sync via the plugin UI or CLI.
- **GPU lifecycle daemon**: Auto-unloads idle models, frees VRAM on demand, and stops the backend when Hermes exits (`systemd` user unit on Linux, Scheduled Task on Windows).
- **Sentence Chunking & WAV Merging**: The Python bridge splits long responses into sentence-level chunks and merges raw PCM into a single clean WAV.

---

## 🛠️ Architecture & Components

| Component | Path | Description |
|-----------|------|-------------|
| **Desktop Plugin UI** | `desktop-plugin/plugin.js` | UI plugin for the Hermes Desktop client (voice select / clone / delete / personas). |
| **TTS Execution Bridge** | `scripts/voicebox_tts.py` | CLI bridge invoked by Hermes for text-to-speech generation (sentence chunking + CUDA OOM retry). |
| **Speak-Stream Adapter** | `scripts/hermes_voicebox_streamer.py` | Registers `voicebox` + `fish` with Hermes' streaming registry for chunked read-aloud. |
| **Fish Audio Provider** | `scripts/fish_tts.py` | Hosted provider: voice cloning, synthesis, cross-profile key/cache resolution. |
| **GPU Lifecycle Daemon** | `scripts/voicebox_gpu.py` | Idle unload / stop-on-exit + localhost control API. |
| **Voicebox API Server** | `scripts/voicebox_server.py` | FastAPI REST backend providing profile CRUD & audio generation on port `17493`. |
| **Installers** | `install.sh` / `install.ps1` → `install.py` | Cross-platform installer (multi-profile aware, idempotent). |

### Install-time Hermes Agent patches

When `$HERMES_HOME/hermes-agent` exists, the installer applies marked patches:

| Target | Marker |
|--------|--------|
| `tools/voicebox_command_streamer.py` | Copied adapter file |
| `tools/tts_streaming.py` | `# BEGIN hermes-voicebox-streamer` … `# END` |
| `hermes_cli/web_server.py` | `# BEGIN hermes-voicebox-prefetch` … `# END` |

Re-run the installer after Hermes Agent updates (those files get overwritten).

---

## ⚙️ REST API Endpoints (Port 17493)

- `GET /health`: Health check endpoint.
- `GET /profiles`: All available preset and custom voice profiles.
- `GET /profiles/{id}`: Metadata for a specific profile.
- `GET /settings/active-voice` / `PUT /settings/active-voice`: Active voice selection.
- `POST /profiles/upload`: Custom voice audio upload (`.wav`/`.mp3`).
- `DELETE /profiles/{id}`: Remove profile & reference audio.
- `POST /models/preload`: Async model warm-up.
- `POST /generate/stream`: Generate and stream WAV audio.

---

## 🎭 Voice Cloning with Chatterbox (MIT-Commercial)

Voicebox already ships with a Chatterbox backend — you just need to switch a profile to use it:

### Why Chatterbox over Qwen?
- **License**: MIT — safe for commercial products. Qwen is non-commercial (CPML).
- **VRAM**: ~3.3 GB (fits alongside other GPU tasks). Qwen 1.7B needs 7+ GB.
- **Voice quality**: Zero-shot cloning from ~5s of reference audio. Comparable to Qwen.

### How to switch a profile
1. In Voicebox → Profiles, edit a voice profile.
2. Change the default engine from `qwen` to `chatterbox`.
3. Upload reference audio if not already done.
4. Test TTS — audio plays through Chatterbox.

Or via API:
```bash
# Get profile
curl -s http://127.0.0.1:17493/profiles/{profile-id} | python3 -c "
import json,sys
p = json.load(sys.stdin)
print(f'Current engine: {p.get(\"default_engine\")}')
p['default_engine'] = 'chatterbox'
# PUT back
"
```

### VRAM budget (RTX PRO 2000 8GB)
| Engine | VRAM | Notes |
|--------|------|-------|
| Chatterbox | ~3.3 GB | MIT license, voice cloning ✅ |
| Qwen 0.6B | ~2.5 GB | Non-commercial |
| Qwen 1.7B | ~7.5 GB | Non-commercial, may OOM |
| Kokoro | ~400 MB | Presets only, no cloning |
| Fish Audio | 0 GB | Hosted, needs API key |

**Full step-by-step guide (what gets installed, prerequisites, keys, troubleshooting): [`docs/INSTALL.md`](docs/INSTALL.md)**

### One-shot install (recommended)

Linux / macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/chuckatbcs/hermes-voicebox/master/bootstrap.sh | bash -s -- --one-click
```

Windows (PowerShell):

```powershell
git clone https://github.com/chuckatbcs/hermes-voicebox.git; cd hermes-voicebox
.\install.ps1 --one-click
```

`install.sh` / `install.ps1` are launchers for the full cross-platform
installer (`install.py`) and pass every flag through (`-y`, `--skip-prereqs`,
`--all-profiles`, …). After installing, **restart Hermes Desktop**, open the
**Voicebox Control** sidebar panel, and pick a voice. Optional hosted TTS:
paste a free [fish.audio](https://fish.audio) API key in the plugin UI.

### Manual (from a cloned repo)

```bash
chmod +x install.sh
./install.sh                # interactive — approve each provisioning step
```

---

## 🔧 Configuration

The installer writes a marked `tts:` block into each profile's `config.yaml`:

```yaml
tts:
  provider: voicebox        # or: fish (hosted)
  providers:
    voicebox:
      type: command
      command: python3 ~/.hermes/scripts/voicebox_tts.py --text-file {input_path} --out {output_path} --voice {voice}
    fish:
      type: command
      command: python3 ~/.hermes/scripts/voicebox_tts.py --provider fish --fish-label jarvis ...
      api_key: <your Fish Audio key>   # entered via the plugin UI
```

Switch providers any time from the **Voicebox Control** panel in the Hermes Desktop sidebar. The Fish API key is stored locally in your config; nothing is sent anywhere except Fish's own API during synthesis.

---

## 🧪 Testing & Verification

Full battery (Linux):

```bash
python3 -m py_compile install.py installer/prereqs.py scripts/*.py
python3 -m unittest test_install.py -v
cd scripts && python3 -m unittest test_voicebox_tts.py test_voicebox_gpu.py test_hermes_voicebox_streamer -v
python3 scripts/test_e2e_root_to_tip.py        # multi-profile E2E
node --test desktop-plugin/test_plugin_storage.mjs
```

CI runs the same suites on Windows (self-hosted runner) via `.github/workflows/cross-platform.yml`. Service-backend behavior (systemd / schtasks) is exercised on every host through mocked backends, per the repo's parity rules.

Live smoke test after install:

```bash
curl http://127.0.0.1:17493/health
python3 ~/.hermes/scripts/voicebox_tts.py --text "Hello world." --out /tmp/out.wav --voice default
python3 -c "import wave; w=wave.open('/tmp/out.wav'); print(round(w.getnframes()/w.getframerate(),2), 'seconds')"
```

---

## 📄 License

MIT — see [LICENSE](LICENSE).
