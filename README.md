# Hermes Voicebox Integration

A complete, end-to-end integration between the **Hermes Desktop Client** and **Voicebox API**. Includes a desktop plugin for managing voice profiles (Select/Upload/Delete), an intelligent python-based execution bridge, and an automated Voicebox API REST backend server (`voicebox_server.py`).

---

## 🚀 Features

- **Automated Backend & Server**: Built-in FastAPI Voicebox API server (`voicebox_server.py`) serving on `http://127.0.0.1:17493` with CORS support for Hermes Desktop UI.
- **Voice Profile Management**: Supports profile listing, active voice selection persistence, custom audio upload (`.wav`/`.mp3`), and safe profile deletion.
- **Smart Engine Routing**: Automatically maps resource usage based on voice profile engines:
  - 🟢 **Kokoro 82M** (~400 MB VRAM) for silent, ultra-fast daily TTS using presets.
  - 🟡 **Chatterbox 3B / Turbo** (~4 GB VRAM) for lightweight voice cloning.
  - 🔴 **Qwen TTS 1.7B** (~7.6 GB VRAM) for highest fidelity voice cloning.
- **Sentence Chunking & WAV Merging**: The python bridge (`voicebox_tts.py`) splits long responses into sentence-level chunks, streams generation, and merges raw PCM data into a single, clean WAV output.

---

## 🛠️ Architecture & Components

| Component | Path | Description |
|-----------|------|-------------|
| **Desktop Plugin UI** | `desktop-plugin/plugin.js` | React UI plugin for Hermes Desktop client. |
| **TTS Execution Bridge** | `scripts/voicebox_tts.py` | CLI bridge invoked by Hermes for text-to-speech generation. |
| **Voicebox API Server** | `scripts/voicebox_server.py` | FastAPI REST backend server providing profile CRUD & audio streaming on port `17493`. |
| **Windows Launcher** | `install.ps1` / `start_backend.ps1` | Automated installer and background service launcher. |

---

## ⚙️ REST API Endpoints (Port 17493)

- `GET /health`: Health check endpoint.
- `GET /profiles`: Returns all available preset and custom voice profiles.
- `GET /profiles/{id}`: Returns metadata for a specific voice profile.
- `GET /settings/active-voice`: Returns currently active voice ID.
- `PUT /settings/active-voice`: Updates active voice selection.
- `POST /profiles/upload`: Handles custom voice audio upload (`.wav`/`.mp3`).
- `DELETE /profiles/{id}`: Removes voice profile & reference audio.
- `POST /generate/stream`: Generates and streams WAV audio data for requested text & profile.

---

## 💻 Installation & Quick Start

### Windows

1. Clone or open this repository.
2. Run PowerShell installer:
   ```powershell
   ./install.ps1
   ```
   *This automatically copies plugin & bridge files to `%USERPROFILE%\.hermes\`, checks/installs dependencies (`fastapi`, `uvicorn`, `numpy`), patches `%USERPROFILE%\.hermes\config.yaml`, and launches the Voicebox API backend server on port `17493`.*

3. Manual Launcher (optional):
   ```powershell
   ./start_backend.ps1
   ```

### Linux (Pop!_OS / Ubuntu / Debian)

1. Run the bash installer:
   ```bash
   chmod +x install.sh
   ./install.sh
   ```

---

## 🧪 Testing & Verification

Run the built-in test suite:
```bash
python -m unittest discover -s tests -p "test_*.py"
```

All 5 test suites cover:
- CLI flag & environment variable precedence (`VOICEBOX_PORT`).
- Sentence splitting & character boundary limits.
- WAV header parsing and PCM chunk assembly.
- End-to-end bridge execution against a mock Voicebox API server.
