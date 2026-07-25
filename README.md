# Hermes Voicebox Integration

A powerful cross-platform integration between the **Hermes Desktop Client** and **Voicebox API**. Includes a desktop plugin for managing voice profiles (Select/Upload/Delete) and an intelligent python-based execution bridge.

## Features

- **Smart Engine Routing**: Automatically flags resource usage based on the profile engine:
  - Kokoro 82M (~400 MB VRAM) for silent, ultra-fast daily TTS using presets.
  - Chatterbox 3B / Turbo (~4 GB VRAM) for lightweight voice cloning.
  - Qwen TTS 1.7B (~7.6 GB VRAM) for highest fidelity voice cloning.
- **Sentence Chunking & WAV Merging**: The python bridge splits long responses into sentence-level chunks, generates them on GPU, and merges them back into a single WAV seamlessly. This avoids timeouts on long AI responses.
- **Native Audio File Upload**: Fully supports cloning voices using native OS audio file upload.
- **Safe Voice Deletion**: Offers a 2-step verification delete mechanism that cleans database records and active configuration states to prevent errors.

---

## Prerequisites & Requirements

### Voicebox Backend
- A running **Voicebox API instance** with GPU support serving on `http://127.0.0.1:17493` (by default).

### Desktop Client & Environment
- **Hermes Desktop** with developer mode / custom plugins enabled.
- **Python 3.10+** on PATH for the machine running Hermes (`python3` on Linux, `python` or `py -3` on Windows).

---

## Configuration & Environment Variables

- **`VOICEBOX_PORT`**: Environment variable to set the port for the Voicebox API backend (default: `17493`).
- **`--base-url`**: CLI argument for `voicebox_tts.py` to set the backend base URL directly (e.g. `--base-url http://127.0.0.1:17493`).
- **`HERMES_DIR`**: Optional override for the Hermes config/plugin directory (default: `~/.hermes`).
- **`localStorage.voicebox_backend_url`**: Optional desktop-plugin override for the Voicebox API base URL (default: `http://127.0.0.1:17493`).

The TTS bridge resolves the backend base URL using the following precedence order:
1. `--base-url` CLI flag (highest precedence)
2. `VOICEBOX_PORT` environment variable
3. Default port `17493` (`http://127.0.0.1:17493`)

---

## Installation

The same installer runs on **Windows** and **Linux**. It copies the plugin + bridge into your Hermes directory and writes a machine-specific TTS config snippet using **absolute paths** (so spaces in usernames work).

### Linux (Ubuntu / Pop!_OS / Debian)

```bash
chmod +x install.sh
./install.sh
```

### Windows (PowerShell)

From the cloned repo folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Or, if script execution is already allowed:

```powershell
.\install.ps1
```

### What the installer does

1. Copies `desktop-plugin/plugin.js` → `%USERPROFILE%\.hermes\desktop-plugins\voice-switcher\plugin.js` (Windows) or `$HOME/.hermes/desktop-plugins/voice-switcher/plugin.js` (Linux)
2. Copies `scripts/voicebox_tts.py` → `.../.hermes/scripts/voicebox_tts.py`
3. Detects a working Python 3.10+ launcher on PATH
4. Writes `voicebox-provider.snippet.yaml` with absolute paths for this PC
5. Merges that snippet into `config.yaml` when safe:
   - creates `config.yaml` if missing
   - replaces a previous `# BEGIN hermes-voicebox` … `# END hermes-voicebox` block
   - appends when there is no existing `tts:` section
   - otherwise leaves your config alone and prints the snippet to merge manually (backup: `config.yaml.bak`)

### Installer options

```bash
# Linux
./install.sh --no-config
./install.sh --hermes-dir /custom/path/.hermes
./install.sh --print-snippet

# Windows
.\install.ps1 -NoConfig
.\install.ps1 -HermesDir "D:\HermesData\.hermes"
.\install.ps1 -PrintSnippet
```

You can also run the shared installer directly on either OS:

```bash
python3 install.py
python install.py
```

### After install

1. Ensure Voicebox API is running at `http://127.0.0.1:17493`
2. Restart Hermes Desktop so it reloads plugins and config
3. Open the **Voicebox** sidebar entry

Example generated config (paths differ per machine):

```yaml
# BEGIN hermes-voicebox
tts:
  provider: voicebox
  providers:
    voicebox:
      type: command
      command: python3 /home/you/.hermes/scripts/voicebox_tts.py --text-file {input_path} --out {output_path} --voice {voice}
      voice: default
      output_format: wav
# END hermes-voicebox
```

On Windows the command line will use `python` or `py -3` plus your absolute `C:\Users\...\voicebox_tts.py` path.
