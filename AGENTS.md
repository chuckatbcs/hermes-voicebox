# AGENTS.md — Hermes Voicebox Integration

## Role

Bounded local execution engineer for this repository unless explicitly assigned otherwise.

## Source of truth (read in order)

1. `AGENTS.md` (this file)
2. `README.md` — product overview, install, and configuration
3. Existing install scripts and runtime entrypoints listed below

## Repository layout

| Path | Purpose |
|------|---------|
| `desktop-plugin/plugin.js` | Hermes desktop plugin UI (voice select / clone / delete / personas; per-profile binding; `defaultEnabled: false`) |
| `desktop-plugin/plugin_storage.mjs` | Storage/migration helpers (unit-tested; mirrored inline in `plugin.js`) |
| `scripts/voicebox_tts.py` | Hermes TTS command bridge (clone sentence chunking + WAV merge; CUDA OOM unload/retry at 0.6B) |
| `scripts/hermes_voicebox_streamer.py` | Hermes speak-stream adapter (sentence PCM + look-ahead; installed as `voicebox_command_streamer.py`) |
| `scripts/voicebox_bind.py` | CLI helper to bind voice → `$HERMES_HOME` config + `voicebox_binding.json` |
| `scripts/voicebox_gpu.py` | GPU lifecycle: unload / idle / stop-on-Hermes-exit + localhost control API |
| `scripts/diagnose_tts.sh` | TTS + GPU + MCP voicebox health trace (installer copies to `$HERMES_HOME/scripts/`) |
| `installer/systemd/voicebox-gpu-lifecycle.service` | Linux user unit for the lifecycle daemon |
| `installer/windows/voicebox-gpu-lifecycle.xml` | Windows Scheduled Task definition for the same daemon (parity twin) |
| `install.py` | Cross-platform installer entrypoint (`--profile` / `--all-profiles`) |
| `install.sh` / `install.ps1` | OS launchers (bootstrap Python, then `install.py`) |
| `installer/` | Prerequisite detection + provisioning (Hermes, Voicebox, models) |
| `README.md` | User-facing docs |

### Install-time Hermes Agent patches

When `$HERMES_HOME/hermes-agent` exists, `install_speak_stream_hook()` applies:

| Target under `hermes-agent/` | Marker / artifact |
|------------------------------|-------------------|
| `tools/voicebox_command_streamer.py` | Copied from `scripts/hermes_voicebox_streamer.py` |
| `tools/tts_streaming.py` | `# BEGIN hermes-voicebox-streamer` … `# END hermes-voicebox-streamer` |
| `hermes_cli/web_server.py` | `# BEGIN hermes-voicebox-prefetch` … `# END hermes-voicebox-prefetch` |

Return codes / suffixes: `missing_src`, `no_hermes_agent`, `produce_pattern_miss`, `produce_patched`, `produce_replaced`, `produce_skipped`. Re-run install after Hermes updates (those files get overwritten).

Also written into profile `config.yaml`: marked TTS block with `timeout: 600`, sample personalities (strips corrupt bare `-personalities` lines).

## Authorized scope

Agents may:

- Fix bugs and apply performance/reliability optimizations in the plugin, bridge, installers, and docs
- Extend the installer to detect/provision prerequisites via **official upstream installers and public APIs** (Hermes install scripts, Voicebox releases/Docker, `/models/*`)
- Add focused tests for bridge/installer helpers when practical
- Update README when behavior or config contracts change

Agents must not:

- Vendor or fork Voicebox/Hermes application source into this repo
- Commit secrets, credentials, tokens, or local machine paths
- Merge PRs, deploy, or change live systems without explicit user authorization
- Work directly on `master` unless the user explicitly authorizes it

## Protected files

None currently designated. Do not invent protected paths.

## Branch and GitHub workflow

1. Create feature branches from the authorized base (`master` unless specified).
2. Prefer branch names matching the environment template when provided by the cloud agent.
3. Never use `git add -A`; stage explicit paths only.
4. Do not force-push, rebase shared branches, or rewrite published history unless explicitly authorized.
5. Push the working branch and update the existing PR; do not open duplicates.
6. Do not merge without explicit user authorization.

## Cross-platform parity (Windows + Linux)

This repository installs on **both Windows and Linux from a single branch**.
Treat platform parity as a correctness requirement, not a nice-to-have.

### Service backend abstraction

`install.service_backend()` maps the host to its supervisor. Never re-introduce
a bare `platform.system() != "Linux"` guard in lifecycle code:

| Platform | Backend | Installer function | Template |
|----------|---------|--------------------|----------|
| Linux | `systemd` | `_install_gpu_lifecycle_systemd()` | `installer/systemd/voicebox-gpu-lifecycle.service` |
| Windows | `schtasks` | `_install_gpu_lifecycle_schtasks()` | `installer/windows/voicebox-gpu-lifecycle.xml` |
| other | `None` | config-only | — |

Runtime equivalents in `scripts/voicebox_gpu.py`: `stop_voicebox()` uses
`systemctl --user stop` on Linux and `taskkill /IM Voicebox.exe /T` on Windows;
`start_voicebox()` uses `systemctl --user start` vs a detached `Voicebox.exe`
relaunch via `find_windows_voicebox_exe()`. The daemon registers its shutdown
handler for `SIGTERM`, `SIGBREAK`, and `SIGINT` so both supervisors can stop it
gracefully.

### Rules

1. **Change both templates together.** Any edit to lifecycle behaviour (restart
   policy, shutdown grace, ExecStart arguments) must land in the systemd unit
   *and* the Scheduled Task XML in the same commit, plus the parity table in
   `README.md`.
2. **Never branch on OS inside a test to skip logic.** Mock
   `install.platform.system()` and assert both backends on every host. A
   platform `skipIf` is acceptable only when the live OS supervisor is required.
3. **Never assert on path separators.** Build expectations from `Path`/`str(Path)`
   so `\` vs `/` cannot fail a test.
4. **Status strings are contracts.** `install_gpu_lifecycle()` returns
   `enabled:` / `unit_written:` / `config_only_non_default_home` /
   `config_only_unsupported_platform_<os>` / `missing_unit_template`. Tests and
   installer output depend on these; extend rather than rename.
5. **Line endings are governed by `.gitattributes`.** `.sh`/`.py`/`.service` are
   LF; `.ps1`/`.bat` are CRLF; `installer/windows/*.xml` is binary (UTF-16LE).
   A diff touching every line means your editor rewrote endings — fix with
   `git add --renormalize .` before committing.

### Cross-platform git workflow

Because commits arrive from two operating systems on the same branch:

1. Stage explicit paths only; never `git add -A` (it sweeps `data/`,
   `__pycache__/`, `voicebox_gpu.json`, and generated `*.snippet.yaml`).
2. `git pull --rebase` before pushing from a second machine. Do not merge the
   same feature branch from two OSes in parallel.
3. Confirm `git diff --stat` lists only intended lines — CRLF churn is a
   review blocker.
4. State in the end-of-task report **which OS the checks were run on**, and note
   any check that could not be executed there.

## Validation

Before claiming complete, run applicable checks **on your host OS** and state
which OS that was. The suites exercise both service backends regardless of host.

Linux:

- Python syntax: `python3 -m py_compile scripts/voicebox_tts.py scripts/voicebox_bind.py scripts/voicebox_gpu.py scripts/hermes_voicebox_streamer.py install.py installer/prereqs.py`
- Unit tests: `python3 -m unittest test_install.py -v` and `cd scripts && python3 -m unittest test_voicebox_tts.py test_voicebox_gpu.py -v`
- Root-to-tip E2E (multi-profile install/bind/idempotency): `python3 scripts/test_e2e_root_to_tip.py`
- Installer smoke test: `./install.sh --hermes-dir <tmpdir> --skip-prereqs`

Windows:

- Python syntax: `py -3 -m py_compile install.py installer/prereqs.py scripts/voicebox_tts.py scripts/voicebox_bind.py scripts/voicebox_gpu.py scripts/hermes_voicebox_streamer.py`
- Unit tests: `py -3 -m unittest test_install.py -v` and `cd scripts; py -3 -m unittest test_voicebox_tts.py test_voicebox_gpu.py -v`
- Root-to-tip E2E: `py -3 scripts/test_e2e_root_to_tip.py`
- Installer smoke test: `powershell -ExecutionPolicy Bypass -File .\install.ps1 -SkipPrereqs -HermesDir $env:TEMP\hermes-test`

Both:

- Plugin storage: `node --test desktop-plugin/test_plugin_storage.mjs`
- CI runs the full matrix (ubuntu-latest + windows-latest, py3.10/3.12) via
  `.github/workflows/cross-platform.yml`, including a line-ending hygiene job.
- Manual sanity review of plugin fetch/error paths when UI tests are unavailable

## Stop conditions

Stop and ask for authorization when work would:

- Change public config contracts in a breaking way without documenting them
- Add authentication schemes or expose Voicebox beyond localhost by default
- Vendor/fork Voicebox or Hermes application source into this repository
- Conflict with instructions in this file

## End-of-task report

Every task must report: summary, files changed, checks run, risks, starting/ending SHAs, branch/PR state, working tree, merge status, stop-condition status, next authorized step, and one final timestamp line.
