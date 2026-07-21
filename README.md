# Hermes Voicebox Integration

A powerful cross-platform integration between the **Hermes Desktop Client** and **Voicebox API**. Includes a desktop plugin for managing voice profiles (Select/Record/Upload/Delete) and an intelligent python-based execution bridge.

## 🚀 Features

- **Smart Engine Routing**: Automatically flags resource usage based on the profile engine:
  - 🟢 **Kokoro 82M** (~400 MB VRAM) for silent, ultra-fast daily TTS using presets.
  - 🟡 **Chatterbox 3B / Turbo** (~4 GB VRAM) for lightweight voice cloning.
  - 🔴 **Qwen TTS 1.7B** (~7.6 GB VRAM) for highest fidelity voice cloning.
- **Sentence Chunking & WAV Merging**: The python bridge splits long responses into sentence-level chunks, generates them on GPU, and merges them back into a single WAV seamlessly. This avoids timeouts on long AI responses.
- **Microphone Recording & Audio File Upload**: Fully supports cloning voices using either browser microphone recording or custom audio file upload.
- **Safe Voice Deletion**: Offers a 2-step verification delete mechanism that cleans database records and active configuration states to prevent errors.

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
         command: python3 /home/YOUR_USERNAME/.hermes/scripts/voicebox_tts.py --text-file {input_path} --out {output_path} --voice {voice}
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
         command: python C:\Users\YOUR_USERNAME\.hermes\scripts\voicebox_tts.py --text-file {input_path} --out {output_path} --voice {voice}
         voice: default
         output_format: wav
   ```

---

## 🛠️ Requirements

### Desktop Client
- **Hermes Desktop** with developer mode / custom plugins enabled.

### Voicebox API
- A running **Voicebox API instance** serving on `http://127.0.0.1:17493`.
- Python 3.10+ in the environment executing the bridge script.
