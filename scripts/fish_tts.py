#!/usr/bin/env python3
"""
Fish Audio (hosted) TTS helper for the Hermes-Voicebox bridge.

Provides:
  - clone_voice_from_file(path, title, key) -> dict   (returns voice model id)
  - synthesize(text, voice_id, key, out_path, fmt)    (writes audio file)

Free tier model is ``s2.1-pro-free`` (unlimited under Fair Use). This keeps
Hermes fully local-first: Fish is an OPTIONAL provider selected with
``--provider fish``; nothing leaves the machine unless that flag is passed.

No third-party SDK is required (uses ``requests`` if present, else urllib).
"""
from __future__ import annotations

import base64
import os
import json
import time

import requests  # available in the Hermes/Voicebox environment

API_BASE = "https://api.fish.audio"
TTS_DEFAULT_MODEL = "s2.1-pro-free"

# Local cache of cloned voice ids, keyed by a caller-chosen label.
# We search across ALL Hermes homes (root + every profiles/<name>) because the
# bridge may run under a per-profile HERMES_HOME (e.g. profiles/jarvis) where the
# cache was never written, even though the clone was created under the root home.
_PRIMARY_CACHE_HOME = (
    os.environ.get("HERMES_HOME")
    or os.environ.get("HERMES_DIR")
    or os.path.expanduser("~/.hermes")
)
_VOICE_CACHE = os.path.join(_PRIMARY_CACHE_HOME, "fish_voices.json")


def _candidate_cache_paths() -> list[str]:
    """Root fish_voices.json first, then every profiles/<name>/fish_voices.json."""
    root = os.path.expanduser("~/.hermes")
    paths = [os.path.join(root, "fish_voices.json")]
    profiles_dir = os.path.join(root, "profiles")
    if os.path.isdir(profiles_dir):
        for name in sorted(os.listdir(profiles_dir)):
            p = os.path.join(profiles_dir, name, "fish_voices.json")
            if os.path.isfile(p):
                paths.append(p)
    # Ensure the home the bridge is running under is also considered.
    if os.path.dirname(_VOICE_CACHE) != root:
        paths.append(_VOICE_CACHE)
    # De-dupe while preserving order.
    seen = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


def _write_cache_path() -> str:
    """Where to persist new clones: prefer the running home, else root."""
    if os.path.basename(os.path.dirname(_VOICE_CACHE)) != "profiles":
        return _VOICE_CACHE
    # Running under a profile home — still write to root so it is shared.
    return os.path.join(os.path.expanduser("~/.hermes"), "fish_voices.json")


def _hermes_root() -> str:
    return (
        os.environ.get("HERMES_HOME")
        or os.environ.get("HERMES_DIR")
        or os.path.expanduser("~/.hermes")
    )


def load_fish_key(explicit: str | None = None) -> str | None:
    """
    Resolve the Fish Audio API key from the most convenient source, in order:
      1. explicit argument
      2. FISH_KEY / FISH_API_KEY env vars
      3. <home>/fish_key.txt  (single-line secret; created by installer/UI)
      4. FISH_KEY= in <home>/.env
      5. tts.providers.fish.api_key in <home>/config.yaml (plugin UI write)
    Both the *current* HERMES_HOME (may be a per-profile dir) and the *root*
    ~/.hermes are searched for (3)-(5), because the plugin writes the key to the
    profile it is scoped to, while the bridge may run under a different home.
    Returns the key string, or None if not found.
    """
    if explicit:
        return explicit.strip() or None
    env_key = os.environ.get("FISH_KEY") or os.environ.get("FISH_API_KEY")
    if env_key:
        return env_key.strip() or None

    root = _hermes_root()
    # Candidate homes: the active home first, then the canonical root.
    homes = [root]
    canonical = os.path.expanduser("~/.hermes")
    if os.path.abspath(root) != os.path.abspath(canonical):
        homes.append(canonical)

    for home in homes:
        # 3) dedicated key file
        key_file = os.path.join(home, "fish_key.txt")
        if os.path.isfile(key_file):
            try:
                with open(key_file, "r", encoding="utf-8") as f:
                    val = f.read().strip()
                if val:
                    return val
            except Exception:
                pass

        # 4) .env file
        env_file = os.path.join(home, ".env")
        if os.path.isfile(env_file):
            try:
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("FISH_KEY="):
                            val = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if val:
                                return val
            except Exception:
                pass

        # 5) config.yaml tts.providers.fish.api_key
        cfg_path = os.path.join(home, "config.yaml")
        if os.path.isfile(cfg_path):
            try:
                import yaml  # pyyaml is present in the Hermes runtime
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                tts = cfg.get("tts") or {}
                fish = (tts.get("providers") or {}).get("fish") or {}
                val = fish.get("api_key")
                if val:
                    return str(val).strip() or None
            except Exception:
                pass

    return None


def _api_key(explicit: str | None = None) -> str:
    key = load_fish_key(explicit)
    if not key:
        raise RuntimeError(
            "Fish Audio key missing. Enter it in the Voicebox plugin (TTS Provider "
            "card), set FISH_KEY in the environment, or write it to "
            "~/.hermes/fish_key.txt. Free key from https://fish.audio."
        )
    return key


def _headers(key: str, model: str | None = None) -> dict:
    h = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if model:
        h["model"] = model
    return h


def _post_json(url: str, headers: dict, body: dict | None = None, timeout: int = 120) -> dict:
    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _post_multipart(url: str, headers: dict, files: dict, data: dict, timeout: int = 300) -> dict:
    # Manual multipart/form-data so we avoid an extra dependency.
    boundary = "----fishttsbndry%d" % int(time.time() * 1000)
    parts = []
    for k, v in data.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for k, (fname, fbytes) in files.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n".encode()
            + fbytes + b"\r\n"
        )
    payload = b"".join(parts) + f"--{boundary}--\r\n".encode()
    hdr = dict(headers)
    hdr["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    hdr.pop("model", None)
    r = requests.post(url, headers=hdr, data=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _get_bytes(url: str, headers: dict, body: dict, timeout: int = 120) -> bytes:
    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    r.raise_for_status()
    return r.content


# ---------------------------------------------------------------------------
# Voice cache (so we don't re-clone on every run)
# ---------------------------------------------------------------------------

def load_cached_voices() -> dict:
    # Merge across all candidate homes; later (profile) entries override root
    # only if they define a label the root lacks — root is the canonical store.
    merged: dict = {}
    for path in _candidate_cache_paths():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                merged.update(data)
        except Exception:
            continue
    # Also merge clones persisted by the plugin UI into config.yaml
    # (tts.providers.fish.clones or a top-level fish_clones map), which is how
    # the "Clone to Fish" button records a hosted clone without writing a file.
    # Scan EVERY Hermes home (root + all profiles/*) so a clone written to one
    # profile's config is visible when TTS runs under a different home — without
    # this, a leaf-profile clone could go invisible and fall back to the default
    # (female) voice on a new session.
    try:
        import yaml
        for home in _config_homes():
            cfg_path = os.path.join(home, "config.yaml")
            if not os.path.isfile(cfg_path):
                continue
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            clones = _config_clones_from(cfg)
            merged.update(clones)
    except Exception:
        pass
    return merged


def _config_homes() -> list[str]:
    root = _hermes_root()
    canonical = os.path.expanduser("~/.hermes")
    homes = [root]
    if os.path.abspath(root) != os.path.abspath(canonical):
        homes.append(canonical)
    # Also every profiles/<name> home — the plugin may write the fish provider
    # block (including clones) under a specific profile, and TTS may run under a
    # different one. Scan them all so label->id resolution never misses.
    profiles_dir = os.path.join(canonical, "profiles")
    if os.path.isdir(profiles_dir):
        for name in sorted(os.listdir(profiles_dir)):
            p = os.path.join(profiles_dir, name)
            if os.path.isdir(p):
                homes.append(p)
    # de-dup preserving order
    seen = set()
    out = []
    for h in homes:
        a = os.path.abspath(h)
        if a not in seen:
            seen.add(a)
            out.append(h)
    return out


def _config_clones_from(cfg: dict) -> dict:
    """Extract {label: {voice_id}} clones from a parsed config.yaml."""
    out: dict = {}
    if not isinstance(cfg, dict):
        return out
    tts = cfg.get("tts") or {}
    fish = (tts.get("providers") or {}).get("fish") or {}
    clones = fish.get("clones") or {}
    if isinstance(clones, dict):
        for label, vid in clones.items():
            if isinstance(vid, str) and vid:
                out[label] = {"voice_id": vid}
    # Also accept a top-level fish_clones map.
    top = cfg.get("fish_clones")
    if isinstance(top, dict):
        for label, vid in top.items():
            if isinstance(vid, str) and vid:
                out.setdefault(label, {"voice_id": vid})
    return out


def save_cached_voice(label: str, voice_id: str, meta: dict | None = None) -> None:
    path = _write_cache_path()
    store = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                store = json.load(f)
        except Exception:
            store = {}
    store[label] = {"voice_id": voice_id, **(meta or {})}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)


def get_cached_voice(label: str) -> str | None:
    return load_cached_voices().get(label, {}).get("voice_id")


# Known label overrides: map a local Voicebox voice name (or id fragment) to a
# Fish clone label. Anything not listed falls back to a slugified name.
_LABEL_OVERRIDES = {
    "jarvis": "jarvis",
    "jarvis_new": "jarvis",
    "vincent price": "vincent_price",
    "vincent": "vincent_price",
    "porky pig": "porky_pig",
    "porky": "porky_pig",
    "eric cartman": "cartman",
    "cartman": "cartman",
    "glados": "glados",
    "gla_dos": "glados",
    "michael": "michael",
    "mimi": "mimi",
    "bella": "bella",
    "rachel": "rachel",
}

DEFAULT_FISH_LABEL = "jarvis"


def _slugify(name: str) -> str:
    out = []
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "_", "-", "."):
            out.append("_")
    s = "".join(out)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_") or DEFAULT_FISH_LABEL


def fish_label_for_voice(voice_name: str | None, voice_id: str | None = None) -> str:
    """
    Map a *local* Voicebox voice (name/id) to the Fish clone label used in
    ~/.hermes/fish_voices.json. Returns 'jarvis' if nothing matches (the one
    clone we ship). The plugin passes this as --fish-label so each local voice
    can resolve to its own hosted clone once cloned via Fish.
    """
    if voice_name:
        key = voice_name.strip().lower()
        if key in _LABEL_OVERRIDES:
            return _LABEL_OVERRIDES[key]
        # id fragment match (e.g. a clone id containing 'jarvis')
        for frag, lbl in _LABEL_OVERRIDES.items():
            if frag in key:
                return lbl
        return _slugify(voice_name)
    if voice_id:
        for frag, lbl in _LABEL_OVERRIDES.items():
            if frag in voice_id.lower():
                return lbl
    return DEFAULT_FISH_LABEL


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clone_voice_from_file(
    audio_path: str,
    title: str = "Hermes Jarvis",
    label: str = "jarvis",
    api_key: str | None = None,
    visibility: str = "private",
    train_mode: str = "fast",
) -> str:
    """
    Clone a voice from a local audio file. Returns the Fish voice model id.
    Caches by ``label`` so repeated calls reuse the clone.
    """
    cached = get_cached_voice(label)
    if cached:
        return cached
    key = _api_key(api_key)
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    if not audio_bytes:
        raise RuntimeError(f"Audio file empty: {audio_path}")
    url = f"{API_BASE}/model"
    headers = {"Authorization": f"Bearer {key}"}
    data = {
        "type": "tts",
        "title": title,
        "visibility": visibility,
        "train_mode": train_mode,
        "enhance_audio_quality": "true",
        "generate_sample": "false",
    }
    resp = _post_multipart(
        url, headers, files={"voices": (os.path.basename(audio_path), audio_bytes)}, data=data
    )
    vid = resp.get("_id") or resp.get("id")
    if not vid:
        raise RuntimeError(f"Fish clone returned no id: {resp}")
    save_cached_voice(label, vid, {"title": title, "created": time.time()})
    return vid


def synthesize(
    text: str,
    voice_id: str | None = None,
    api_key: str | None = None,
    out_path: str | None = None,
    audio_format: str = "wav",
    sample_rate: int = 44100,
    model: str | None = None,
    timeout: int = 120,
) -> bytes:
    """
    Synthesize ``text`` via Fish Audio. Returns raw audio bytes.

    If ``voice_id`` is None, Fish uses its built-in default voice.
    """
    key = _api_key(api_key)
    if not text or not text.strip():
        raise RuntimeError("Empty text for Fish TTS.")
    model = model or TTS_DEFAULT_MODEL
    payload = {
        "text": text,
        "format": audio_format,
        "sample_rate": sample_rate,
        "latency": "normal",
    }
    if voice_id:
        payload["reference_id"] = voice_id
    headers = _headers(key, model)
    audio = _get_bytes(f"{API_BASE}/v1/tts", headers, payload, timeout=timeout)
    if not audio or (audio[:4] == b"{" and b"message" in audio[:200]):
        raise RuntimeError(f"Fish TTS returned an error: {audio[:300]!r}")
    if out_path:
        with open(out_path, "wb") as f:
            f.write(audio)
    return audio


def main(argv=None):
    """CLI: clone an audio file to Fish, or synthesize text.

    fish_tts.py clone --audio <file> --label <label> [--title <title>] [--key <key>]
    fish_tts.py synth --text <text> [--voice-id <id>] [--out <path>] [--key <key>]
    """
    import argparse

    p = argparse.ArgumentParser(description="Fish Audio helper (clone / synth).")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("clone", help="Clone a local audio file to a Fish voice.")
    c.add_argument("--audio", default=None, help="Path to the sample audio file (wav/mp3).")
    c.add_argument("--audio-b64", default=None,
                   help="Base64 audio. Use '-' to read base64 from stdin (avoids arg-length limits).")
    c.add_argument("--label", required=True, help="Cache label (e.g. 'jarvis') used to resolve later.")
    c.add_argument("--title", default=None, help="Fish voice title (defaults to 'Hermes <label>').")
    c.add_argument("--key", default=None, help="Fish API key (else resolved from env/config/file).")
    c.add_argument("--train-mode", default="fast", choices=["fast", "balanced", "high"])

    s = sub.add_parser("synth", help="Synthesize text to audio.")
    s.add_argument("--text", required=True)
    s.add_argument("--voice-id", default=None)
    s.add_argument("--out", default="/tmp/fish_cli_out.wav")
    s.add_argument("--key", default=None)

    args = p.parse_args(argv)

    if args.cmd == "clone":
        audio_path = args.audio
        if not audio_path and args.audio_b64:
            import tempfile
            raw = args.audio_b64
            if raw == "-":
                import sys as _sys
                raw = _sys.stdin.read().strip()
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.write(base64.b64decode(raw))
            tmp.close()
            audio_path = tmp.name
        if not audio_path:
            print("error: provide --audio or --audio-b64", file=sys.stderr)
            return 2
        title = args.title or f"Hermes {args.label}"
        vid = clone_voice_from_file(
            audio_path, title=title, label=args.label, api_key=args.key, train_mode=args.train_mode
        )
        print(json.dumps({"label": args.label, "voice_id": vid, "title": title}))
        return 0
    if args.cmd == "synth":
        b = synthesize(args.text, voice_id=args.voice_id, api_key=args.key, out_path=args.out)
        print(f"wrote {len(b)} bytes -> {args.out}")
        return 0
    p.print_help()
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
