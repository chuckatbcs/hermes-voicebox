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
| `scripts/diagnose_tts.sh` | TTS + GPU + MCP voicebox health trace |
| `installer/systemd/voicebox-gpu-lifecycle.service` | Linux user unit for the lifecycle daemon |
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

## Validation

Before claiming complete, run applicable checks:

- Python syntax: `python3 -m py_compile scripts/voicebox_tts.py scripts/voicebox_bind.py scripts/voicebox_gpu.py scripts/hermes_voicebox_streamer.py install.py installer/prereqs.py`
- Unit tests: `python3 -m unittest test_install.py -v` and `cd scripts && python3 -m unittest test_voicebox_tts.py test_voicebox_gpu.py -v`
- Plugin storage: `node --test desktop-plugin/test_plugin_storage.mjs`
- Root-to-tip E2E (multi-profile install/bind/idempotency): `python3 scripts/test_e2e_root_to_tip.py`
- Installer smoke test: `./install.sh --hermes-dir <tmpdir> --skip-prereqs`
- Manual sanity review of plugin fetch/error paths when UI tests are unavailable

## Stop conditions

Stop and ask for authorization when work would:

- Change public config contracts in a breaking way without documenting them
- Add authentication schemes or expose Voicebox beyond localhost by default
- Vendor/fork Voicebox or Hermes application source into this repository
- Conflict with instructions in this file

## End-of-task report

Every task must report: summary, files changed, checks run, risks, starting/ending SHAs, branch/PR state, working tree, merge status, stop-condition status, next authorized step, and one final timestamp line.
