# Hermes Voicebox Integration

A powerful cross-platform integration between the **Hermes Desktop Client** and **Voicebox API**. Includes a desktop plugin for managing voice profiles (Select/Upload/Delete) and an intelligent python-based execution bridge.

## 🚀 Features

- **Smart Engine Routing**: Automatically flags resource usage based on the profile engine:
  - 🟢 **Kokoro 82M** (~400 MB VRAM) for silent, ultra-fast daily TTS using presets.
  - 🟡 **Chatterbox 3B / Turbo** (~4 GB VRAM) for lightweight voice cloning.
  - 🔴 **Qwen TTS 1.7B** (~7.6 GB VRAM) for highest fidelity voice cloning.
- **Sentence Chunking & WAV Merging**: The python bridge splits long responses into sentence-level chunks, generates them on GPU, and merges them back into a single WAV seamlessly. This avoids timeouts on long AI responses.
- **Native Audio File Upload**: Fully supports cloning voices using native OS audio file upload.
- **Safe Voice Deletion**: Offers a 2-step verification delete mechanism that cleans database records and active configuration states to prevent errors.

---

## 🛠️ Prerequisites & Requirements

### Voicebox Backend
- A running **Voicebox API instance** with GPU support serving on `http://127.0.0.1:17493` (by default).

### Desktop Client & Environment
- **Hermes Desktop** with developer mode / custom plugins enabled.
- Python 3.10+ in the environment executing the bridge script.

---

## ⚙️ Configuration & Environment Variables

- **`VOICEBOX_PORT`**: Environment variable to set the port for the Voicebox API backend (default: `17493`).
- **`--base-url`**: CLI argument for `voicebox_tts.py` to set the backend base URL directly (e.g. `--base-url http://127.0.0.1:17493`).

The TTS bridge resolves the backend base URL using the following precedence order:
1. `--base-url` CLI flag (highest precedence)
2. `VOICEBOX_PORT` environment variable
3. Default port `17493` (`http://127.0.0.1:17493`)

---

## 💻 Installation

### Option 1: Linux (Ubuntu/Pop!_OS/Debian)

1. Clone this repository locally.
2. Run the install script:
   ```bash
   chmod +x install.sh
   ./install.sh
   ```
3. Update your `$HOME/.hermes/config.yaml` to point to the bridge:
   ```yaml
   tts:
     provider: voicebox
     providers:
       voicebox:
         type: command
         command: python3 $HOME/.hermes/scripts/voicebox_tts.py --text-file {input_path} --out {output_path} --voice {voice}
         voice: default
         output_format: wav
   ```

### Option 2: Windows

1. Clone this repository locally.
2. Open PowerShell and run:
   ```powershell
   ./install.ps1
   ```
3. Update your `%USERPROFILE%\.hermes\config.yaml` to point to the bridge:
   ```yaml
   tts:
     provider: voicebox
     providers:
       voicebox:
         type: command
         command: python %USERPROFILE%\.hermes\scripts\voicebox_tts.py --text-file {input_path} --out {output_path} --voice {voice}
         voice: default
         output_format: wav
   ```
