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

## 💻 Installation & Quick Start

### Linux

```bash
git clone https://github.com/chuckatbcs/hermes-voicebox.git
cd hermes-voicebox
chmod +x install.sh
./install.sh            # add --skip-prereqs to skip model provisioning
```

Then restart Hermes Desktop so it loads the plugin and patched stream hooks.

### Windows

```powershell
git clone https://github.com/chuckatbcs/hermes-voicebox.git
cd hermes-voicebox
./install.ps1
```

The installer copies plugin + bridge files into `%USERPROFILE%\.hermes\`, checks dependencies, patches `config.yaml`, and installs the GPU-lifecycle scheduled task.

### Multi-profile installs

```bash
./install.sh --skip-prereqs --all-profiles -y
```

Installs into `$HERMES_HOME` and every `$HERMES_HOME/profiles/<name>/`.

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
