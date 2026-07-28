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
| `desktop-plugin/plugin.js` | Hermes desktop plugin UI (voice select / clone / delete / personas) |
| `scripts/voicebox_tts.py` | Hermes TTS command bridge (chunked generate + WAV merge) |
| `install.py` | Cross-platform installer entrypoint |
| `installer/` | Prerequisite detection + provisioning (Hermes, Voicebox, models) |
| `install.sh` / `install.ps1` | OS launchers (bootstrap Python, then `install.py`) |
| `README.md` | User-facing docs |

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

- Python syntax: `python3 -m py_compile scripts/voicebox_tts.py install.py installer/prereqs.py`
- Unit tests: `python3 -m unittest test_install.py -v` and `cd scripts && python3 -m unittest test_voicebox_tts.py -v`
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

## Cursor Cloud specific instructions

- Pure Python, standard-library only (no `requirements.txt`, no `package.json`). The only runtime dependency is Python 3.10+ (the VM ships 3.12). There is nothing to `pip install`; the startup update script is a no-op interpreter check.
- Standard checks live in `README.md` (Development / validation) and this file (Validation): `py_compile`, `python3 -m unittest test_install.py -v`, and `(cd scripts && python3 -m unittest test_voicebox_tts.py -v)`. `test_voicebox_tts.py` must be run from inside `scripts/` (it imports `voicebox_tts` directly).
- Runnable pieces in this repo are the installer CLI (`install.py` / `install.sh`) and the TTS bridge (`scripts/voicebox_tts.py`). Use a throwaway `--hermes-dir` (e.g. `./install.sh --hermes-dir /tmp/hermes-test --skip-prereqs`) so you never touch a real `~/.hermes`.
- The TTS bridge talks to a Voicebox backend on `http://127.0.0.1:17493`; neither Voicebox nor Hermes Desktop is installable in the cloud VM. To exercise the bridge end-to-end without real Voicebox, run it against a small stdlib mock HTTP server that answers `GET /health`, `GET /profiles`, `GET /profiles/{id}` (return a preset profile with `preset_voice_id` + `default_engine` so preflight passes), `GET /profiles/{id}/samples`, and `POST /generate/stream` (return a valid WAV per chunk). Feed >800 chars to force multi-chunk generation and verify the merged WAV.
- `desktop-plugin/plugin.js` runs inside the Hermes Desktop GUI (not available here), so it cannot be manually exercised in the cloud VM; review its fetch/error paths statically instead.
