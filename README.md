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

One command. The installer copies the **plugin + bridge** from this repo into
your Hermes home, merges config, enables the plugin automatically, and
(best-effort) provisions Hermes + Voicebox + models. If Docker or model
downloads fail, the plugin is **still installed and usable** — the script
reports what's missing instead of aborting.

> **The installer is self-healing and re-runnable.** Every step is idempotent.
> If a `git clone` was interrupted or a previous install is broken, just run
> the installer again — it repairs/updates instead of failing. (See
> [Repair / update](#repair--update) below.)

> **Prerequisites (already present on a normal Ubuntu desktop):** `git` and
> `python3` (3.10+). A minimal/cloud VM may lack them — the installer can
> install both via `apt`/`dnf`/`pacman` with sudo, or you can pre-install:
> `sudo apt-get install -y git python3`.

### Linux — one line (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/chuckatbcs/hermes-voicebox/master/bootstrap.sh | bash
```

This fetches `install.sh`, which clones/updates the repo (self-healing a
partial clone) and runs the install. **To re-run or update later, just run the
same line again** — it repairs/updates in place.

Manual equivalent (if you'd rather clone yourself):

```bash
git clone https://github.com/chuckatbcs/hermes-voicebox.git
cd hermes-voicebox
python3 install.py --one-click
```

After it finishes, **restart Hermes Desktop** and open the **Voicebox**
sidebar — the plugin is enabled automatically, no manual toggle.

### Windows (PowerShell)

```powershell
git clone https://github.com/chuckatbcs/hermes-voicebox.git
cd hermes-voicebox
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Yes
```

`--one-click` / `-Yes` is fully non-interactive and fault-tolerant: it
auto-approves downloads (models can be multiple GB) and keeps going if a
backend can't be provisioned.

**The installer is self-healing and re-runnable.** Every step
(install plugin, bridge, config merge, plugin-enable, GPU lifecycle) is
idempotent — running it again repairs a partial or broken previous install
instead of failing. If a `git clone` was interrupted, just run the installer
again: it updates an existing clone, or repairs a half-finished one
(backing up the broken folder to `hermes-voicebox.bak.*`).

**To repair / update an existing install:**
```bash
cd hermes-voicebox
python3 install.py --self-heal     # prints what's broken, then fixes it
# or simply re-run the normal install — it overwrites/repairs as needed:
python3 install.py --one-click
```

**Troubleshooting `./install.sh: No such file or directory`:** this almost
always means you ran the command from a directory that isn't the cloned repo
(you must `cd hermes-voicebox` first, or clone it first). Re-run
`install.sh` from the repo, or use `python3 install.py --one-click`, which
doesn't depend on the shell script. In the rare case the clone checked out
with Windows line endings (a global `git config core.autocrlf` mismatch),
re-clone after `git config --global core.autocrlf input`.

If Voicebox isn't running yet, start it (desktop app or
`docker compose -f ~/.hermes/vendor/voicebox up -d`) and re-run
`python install.py -y --skip-hermes` to fetch models + demo voices.

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

The installer wires the lifecycle daemon into the host's own service manager, so
Windows and Linux get the same behaviour through different supervisors:

| Platform | Service backend | Unit installed | Template in repo |
|----------|-----------------|----------------|------------------|
| **Linux** | user **systemd** | `~/.config/systemd/user/voicebox-gpu-lifecycle.service` | `installer/systemd/voicebox-gpu-lifecycle.service` |
| **Windows** | **Task Scheduler** | Scheduled Task `Hermes_Voicebox_GPU_Lifecycle` (at logon) | `installer/windows/voicebox-gpu-lifecycle.xml` |
| **macOS / other** | none | config only — run `lifecycle-daemon` manually | — |

Both platforms expose the localhost control API on `127.0.0.1:17494`. Skip with
`--skip-gpu-lifecycle` (`-SkipGpuLifecycle` on `install.ps1`). Keep Voicebox
running after Hermes quits with `--no-stop-voicebox-on-hermes-exit`.

Behavioural parity between the two unit templates — **change both together**:

| Concern | systemd | Scheduled Task |
|---------|---------|----------------|
| Autostart | `WantedBy=default.target` | `<LogonTrigger>` |
| Restart on crash | `Restart=on-failure`, `RestartSec=5` | `<RestartOnFailure>` `PT1M` × 3 |
| Graceful stop | `KillSignal=SIGTERM`, `TimeoutStopSec=10` | terminate → `SIGBREAK`/`SIGTERM` handler |
| Stop Voicebox | `systemctl --user stop voicebox.service` | `taskkill /IM Voicebox.exe /T` |
| Start Voicebox | `systemctl --user start voicebox.service` | relaunch detached `Voicebox.exe` |

```bash
# Linux
python3 ~/.hermes/scripts/voicebox_gpu.py status
python3 ~/.hermes/scripts/voicebox_gpu.py unload
python3 ~/.hermes/scripts/voicebox_gpu.py stop    # unload + systemctl/docker stop
python3 ~/.hermes/scripts/voicebox_gpu.py config --stop-on-hermes-exit off
```

```powershell
# Windows (same subcommands)
py -3 $env:USERPROFILE\.hermes\scripts\voicebox_gpu.py status
py -3 $env:USERPROFILE\.hermes\scripts\voicebox_gpu.py unload
py -3 $env:USERPROFILE\.hermes\scripts\voicebox_gpu.py stop    # unload + taskkill/docker stop
schtasks /Query /TN Hermes_Voicebox_GPU_Lifecycle              # inspect the task
```


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

This project ships installers for **both Windows and Linux from one branch**.
Every change must keep both paths working; the suites below run on either host
and exercise *both* service backends regardless of which OS you are on.

### Linux

```bash
python3 -m py_compile install.py installer/prereqs.py \
  scripts/voicebox_tts.py scripts/voicebox_bind.py scripts/voicebox_gpu.py \
  scripts/hermes_voicebox_streamer.py
python3 -m unittest test_install.py -v
(cd scripts && python3 -m unittest test_voicebox_tts.py test_voicebox_gpu.py -v)
python3 scripts/test_e2e_root_to_tip.py          # root-to-tip, exits non-zero on failure
./install.sh --hermes-dir /tmp/hermes-test --skip-prereqs
```

### Windows (PowerShell)

```powershell
py -3 -m py_compile install.py installer/prereqs.py `
  scripts/voicebox_tts.py scripts/voicebox_bind.py scripts/voicebox_gpu.py `
  scripts/hermes_voicebox_streamer.py
py -3 -m unittest test_install.py -v
cd scripts; py -3 -m unittest test_voicebox_tts.py test_voicebox_gpu.py -v; cd ..
py -3 scripts/test_e2e_root_to_tip.py
powershell -ExecutionPolicy Bypass -File .\install.ps1 -SkipPrereqs -HermesDir $env:TEMP\hermes-test
```

### Cross-platform test policy

* **No platform-gated skips for logic that can be simulated.** Tests mock
  `install.platform.system()` so the systemd *and* Scheduled Task backends are
  both asserted on every host. A Windows-only or Linux-only `skipIf` is only
  acceptable when the test genuinely needs the live OS supervisor.
* **Never assert on path separators.** Compare against `str(Path(...))` or use
  `pathlib`, so `\` vs `/` never fails a test.
* **Both unit templates must exist in every checkout.** `test_e2e_root_to_tip.py`
  asserts the presence of `installer/systemd/*.service` *and*
  `installer/windows/*.xml`, so deleting one on the other OS fails the suite.
* **Line endings are enforced by `.gitattributes`** — `.sh`/`.py` stay LF,
  `.ps1`/`.bat` stay CRLF. Never commit a whole-file diff caused by a rewrite;
  if `git diff` shows every line changed, your editor changed the endings.
* **Run the E2E before pushing**, on whichever OS you are on. It exits non-zero
  on any failure and prints a per-check table.

### Cross-platform git workflow

Both OSes commit to the same branch, so keep changes reviewable:

1. Stage explicit paths only — never `git add -A` (it sweeps up
   `data/`, `__pycache__/`, and local `voicebox_gpu.json`).
2. When you touch lifecycle behaviour, update **both** templates
   (`installer/systemd/` and `installer/windows/`) plus the parity table above
   in the same commit.
3. Before pushing from a second machine, `git pull --rebase` — do not merge the
   same feature branch from two OSes in parallel.
4. Verify no CRLF churn leaked in: `git diff --stat` should list only the lines
   you meant to change.

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
