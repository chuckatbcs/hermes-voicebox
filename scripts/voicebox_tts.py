#!/usr/bin/env python3
"""
Hermes-Voicebox TTS Bridge
Splits long text into sentence chunks, generates each via /generate/stream,
concatenates the raw WAV data, and writes a single output file.
"""
import sys
import os
import re
import argparse
import json
import struct
import subprocess
import time
import urllib.request
import urllib.error
import urllib.parse

# Optional hosted provider (Fish Audio). Imported lazily-guarded so the bridge
# keeps working when the module is absent (pure-local deployments).
try:
    from fish_tts import synthesize as _fish_synthesize, get_cached_voice as _fish_cached_voice
    _HAVE_FISH = True
except Exception:  # pragma: no cover - optional dependency
    _HAVE_FISH = False

# ---------------------------------------------------------------------------
# Tunables & Configuration
# ---------------------------------------------------------------------------
# Per-chunk HTTP timeout (seconds).  Each chunk is ≤MAX_CHARS characters;
# on Blackwell GPU a 800-char chunk takes ~30-60 s.
CHUNK_TIMEOUT   = 300
# Maximum characters per chunk for preset/fast engines (Kokoro, etc.).
MAX_CHARS       = 800
# Cloning engines (Chatterbox / Qwen) often hit early EOS mid-paragraph.
# Prefer one sentence per request so a truncated generation only loses that
# sentence, not the rest of the reply.
CLONE_SENTENCE_MAX_CHARS = 400

_CLONE_ENGINES = frozenset({
    "chatterbox",
    "chatterbox_turbo",
    "qwen",
    "qwen_fast",
    "qwen3",
})

# Voicebox removed standalone engine id `qwen_fast` (0.6B is now model_size on
# `qwen`). Older profiles still store default_engine=qwen_fast and 500 if we
# forward that id on /generate.
_ENGINE_ALIASES = {
    "qwen_fast": "qwen",
    "qwen3": "qwen",
}


def normalize_engine(engine: str | None) -> str | None:
    """Map deprecated Voicebox engine ids to ones the running API accepts."""
    if engine is None:
        return None
    raw = str(engine).strip()
    if not raw:
        return None
    return _ENGINE_ALIASES.get(raw.lower(), raw)


def model_size_for_engine(engine: str | None) -> str | None:
    """Pick a Qwen checkpoint size that fits typical laptop GPUs.

    Voicebox defaults ``qwen`` to ``1.7B`` (~7+ GiB), which OOMs on 8 GiB cards
    (RTX PRO 2000 / similar). Prefer ``0.6B`` unless overridden.

    Override with ``VOICEBOX_QWEN_MODEL_SIZE=1.7B`` (or ``0.6B``) when needed.
    Deprecated ``qwen_fast`` always maps to ``0.6B``.
    """
    if engine is None:
        return None
    raw = str(engine).strip().lower()
    override = (os.environ.get("VOICEBOX_QWEN_MODEL_SIZE") or "").strip()
    if raw in {"qwen", "qwen_fast", "qwen3"}:
        if override in {"0.6B", "1.7B", "1B", "3B"}:
            return override
        # qwen_fast historically meant the small checkpoint.
        if raw == "qwen_fast":
            return "0.6B"
        return "0.6B"
    return None


def _engine_prefers_sentence_chunks(engine: str | None) -> bool:
    if not engine:
        # Unknown engine — assume clone-like (safer for incomplete reads).
        return True
    return str(engine).strip().lower() in _CLONE_ENGINES


def chunk_max_chars_for_engine(engine: str | None) -> int:
    if _engine_prefers_sentence_chunks(engine):
        return CLONE_SENTENCE_MAX_CHARS
    return MAX_CHARS


def get_base_url(cli_base_url: str = None) -> str:
    """
    Resolve Voicebox API base URL.
    Precedence: --base-url flag > VOICEBOX_PORT env var > default 17493.
    """
    if cli_base_url:
        return cli_base_url.rstrip("/")
    port = os.environ.get("VOICEBOX_PORT", "17493")
    return f"http://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# WAV helpers
# ---------------------------------------------------------------------------

def _parse_wav_header(data: bytes):
    """Return (sample_rate, num_channels, bits_per_sample, data_offset, data_len)."""
    if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ValueError("Not a valid WAV file")
    offset = 12
    while offset < len(data) - 8:
        chunk_id   = data[offset:offset+4]
        chunk_size = struct.unpack_from('<I', data, offset+4)[0]
        if chunk_id == b'fmt ':
            num_channels    = struct.unpack_from('<H', data, offset+8+2)[0]
            sample_rate     = struct.unpack_from('<I', data, offset+8+4)[0]
            bits_per_sample = struct.unpack_from('<H', data, offset+8+14)[0]
        if chunk_id == b'data':
            return sample_rate, num_channels, bits_per_sample, offset+8, chunk_size
        offset += 8 + chunk_size + (chunk_size & 1)   # word-align
    raise ValueError("WAV 'data' chunk not found")


def _build_wav(pcm_chunks: list, sample_rate: int, num_channels: int, bits_per_sample: int) -> bytes:
    """Concatenate raw PCM chunks into a single WAV file."""
    pcm = b''.join(pcm_chunks)
    byte_rate    = sample_rate * num_channels * bits_per_sample // 8
    block_align  = num_channels * bits_per_sample // 8
    data_size    = len(pcm)
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + data_size, b'WAVE',
        b'fmt ', 16,
        1,               # PCM
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b'data', data_size,
    )
    return header + pcm


# ---------------------------------------------------------------------------
# Spoken-text cleanup (stage directions / emphasis markup)
# ---------------------------------------------------------------------------
# LLMs (especially theatrical personas like Cartman) often insert stage
# directions the chat UI may render lightly, but Qwen TTS reads them aloud:
#   [sarcastically]  [whiny voice]  (sighs dramatically)  *with emphasis*
# Chatterbox Turbo understands a small set of paralinguistic tags; keep those,
# strip everything else.

_CHATTERBOX_PARALINGUISTIC = frozenset({
    "laugh",
    "chuckle",
    "cough",
    "sigh",
    "gasp",
    "groan",
    "sniff",
    "shush",
    "clear throat",
    "whisper",
})

_BRACKET_TAG_RE = re.compile(r"\[([^\[\]]{1,80})\]")
_PAREN_STAGE_RE = re.compile(
    r"\((?:laughs?|giggles?|chuckles?|snickers?|gasps?|pants?|sighs?|groans?|"
    r"coughs?|clears?\s+throat|whispers?|shouts?|yells?|screams?|"
    r"emphasi[sz](?:e|ing|is)?|angrily|sadly|happily|sarcastically|"
    r"dramatically|maniacally|whiny|in\s+a\s+[\w\s]{1,40}\s+voice)"
    r"(?:\s+[^)]{0,60})?\)",
    flags=re.IGNORECASE,
)
_PAREN_GENERIC_RE = re.compile(r"\(([^)]{1,80})\)")
_MD_EMPHASIS_RE = re.compile(
    r"(\*\*|__)(.+?)\1|(\*|_)(.+?)\3",
    flags=re.DOTALL,
)


def sanitize_spoken_text_for_voicebox(text: str, engine: str | None = None) -> str:
    """Remove stage-direction / emphasis markup that TTS would speak aloud."""
    if not text:
        return ""
    eng = (engine or "").strip().lower()
    keep_turbo_tags = "chatterbox" in eng  # turbo + multilingual chatterbox

    def _bracket_sub(match: re.Match) -> str:
        inner = (match.group(1) or "").strip().lower()
        # Normalize "clear throat" style multi-word tags
        key = re.sub(r"\s+", " ", inner)
        if keep_turbo_tags and key in _CHATTERBOX_PARALINGUISTIC:
            return match.group(0)
        return " "

    out = _BRACKET_TAG_RE.sub(_bracket_sub, text)
    out = _PAREN_STAGE_RE.sub(" ", out)
    # Drop remaining short parentheticals that look like directions (no digits /
    # URLs) — keeps normal prose like "(or so they say)" only when longer... 
    # Actually strip short alpha parentheticals that are direction-like.
    def _paren_generic(match: re.Match) -> str:
        inner = (match.group(1) or "").strip()
        if not inner:
            return " "
        # Keep parentheticals that look like real asides with punctuation/length
        if len(inner) > 48:
            return match.group(0)
        if re.search(r"\d|https?://|www\.", inner, flags=re.IGNORECASE):
            return match.group(0)
        # Short alphabetic stagey asides → drop
        if re.fullmatch(r"[A-Za-z][A-Za-z\s,'-]{0,47}", inner):
            return " "
        return match.group(0)

    out = _PAREN_GENERIC_RE.sub(_paren_generic, out)
    # Leftover markdown emphasis markers (Hermes usually strips these first)
    out = _MD_EMPHASIS_RE.sub(lambda m: m.group(2) or m.group(4) or "", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


# ---------------------------------------------------------------------------
# Text splitting
# ---------------------------------------------------------------------------

def _split_sentences(text: str, max_chars: int) -> list:
    """
    Split *text* into chunks of at most *max_chars* characters, trying to
    break on sentence boundaries (. ! ? followed by whitespace or end-of-string).
    """
    sentence_re = re.compile(r'(?<=[.!?])\s+')
    sentences   = sentence_re.split(text.strip())

    chunks  = []
    current = []
    cur_len = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        slen = len(sentence)

        # If a single sentence is longer than max_chars, hard-split it.
        if slen > max_chars:
            # Flush current buffer first.
            if current:
                chunks.append(' '.join(current))
                current, cur_len = [], 0
            # Hard-split on word boundaries.
            words   = sentence.split()
            segment = []
            seg_len = 0
            for word in words:
                wlen = len(word) + 1  # +1 for the space
                if seg_len + wlen > max_chars and segment:
                    chunks.append(' '.join(segment))
                    segment, seg_len = [], 0
                segment.append(word)
                seg_len += wlen
            if segment:
                chunks.append(' '.join(segment))
            continue

        if cur_len + slen + 1 > max_chars and current:
            chunks.append(' '.join(current))
            current, cur_len = [], 0

        current.append(sentence)
        cur_len += slen + 1

    if current:
        chunks.append(' '.join(current))

    return [c for c in chunks if c.strip()]


def _split_one_sentence_chunks(text: str, max_chars: int) -> list:
    """
    One sentence per chunk (never pack multiple sentences together).

    Isolates Chatterbox/Qwen early-EOS failures to a single sentence so the
    rest of the reply still gets spoken.
    """
    sentence_re = re.compile(r'(?<=[.!?])\s+')
    # Also treat blank lines as hard breaks (paragraphs without terminal punct).
    parts = []
    for para in re.split(r'\n+', text.strip()):
        para = para.strip()
        if not para:
            continue
        parts.extend(s.strip() for s in sentence_re.split(para) if s.strip())

    chunks = []
    for sentence in parts:
        if len(sentence) <= max_chars:
            chunks.append(sentence)
            continue
        # Oversized single sentence — reuse word hard-split from _split_sentences.
        chunks.extend(_split_sentences(sentence, max_chars))
    return chunks


def split_tts_chunks(text: str, engine: str | None = None) -> list:
    """Choose sentence-isolated vs packed chunking based on engine."""
    max_chars = chunk_max_chars_for_engine(engine)
    if _engine_prefers_sentence_chunks(engine):
        return _split_one_sentence_chunks(text, max_chars)
    return _split_sentences(text, max_chars)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_json(url: str) -> dict:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


_INVALID_VOICE_IDS = {
    "",
    "default",
    "undefined",
    "null",
    "none",
    "00000000-0000-0000-0000-000000000000",
}


def _hermes_home() -> str:
    """Profile-scoped Hermes home: HERMES_HOME → HERMES_DIR → ~/.hermes."""
    return (
        os.environ.get("HERMES_HOME")
        or os.environ.get("HERMES_DIR")
        or os.path.expanduser("~/.hermes")
    )


def _hermes_dir() -> str:
    """Back-compat alias for _hermes_home()."""
    return _hermes_home()


def _read_voice_id_from_json(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return ""
        vid = data.get("voice_id") or data.get("profile_id") or data.get("id") or ""
        vid = str(vid).strip()
        if vid and vid.lower() not in _INVALID_VOICE_IDS:
            return vid
    except Exception:
        pass
    return ""


def _read_local_active_voice() -> str:
    """Per-profile binding / legacy sidecar under the active Hermes home."""
    home = _hermes_home()
    candidates = [
        os.path.join(home, "voicebox_binding.json"),
        os.path.join(home, "voicebox_active_voice.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "voicebox_binding.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "voicebox_active_voice.json"),
    ]
    for path in candidates:
        vid = _read_voice_id_from_json(path)
        if vid:
            return vid
    return ""


def _is_uuid(value: str) -> bool:
    """True when value looks like a UUID (e.g. '438cdadd-...')."""
    if not value:
        return False
    try:
        import uuid as _uuid_mod
        _uuid_mod.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def _resolve_profile_by_name(name: str, profiles: list[dict]) -> str | None:
    """Find a Voicebox profile id by case-insensitive name match."""
    needle = name.strip().lower()
    for p in profiles:
        pname = (p.get("name") or "").strip().lower()
        if pname == needle:
            return str(p["id"])
    return None


def resolve_profile_id(cli_voice: str, base_url: str, get_json=None) -> str:
    """
    Resolve which Voicebox profile to use.

    Precedence:
      1) CLI --voice (Hermes substitutes tts.providers.voicebox.voice here)
         - If it's a UUID, use directly.
         - Otherwise, treat as a profile NAME and resolve via /profiles.
      2) Per-profile binding / sidecar under HERMES_HOME
      3) Voicebox /settings/active-voice (demoted — process-global, cross-profile bleed)
      4) First Voicebox profile
    """
    fetcher = get_json or _get_json
    voice = (cli_voice or "").strip()
    if voice and voice.lower() not in _INVALID_VOICE_IDS:
        # If not a UUID, try to resolve as a profile name
        if not _is_uuid(voice):
            try:
                profiles = fetcher(f"{base_url}/profiles")
                if profiles and isinstance(profiles, list):
                    resolved = _resolve_profile_by_name(voice, profiles)
                    if resolved:
                        print(
                            f"Resolved voice name '{voice}' → profile id {resolved}.",
                            file=sys.stderr,
                        )
                        return resolved
                    available = [
                        f"{p.get('name')}" for p in profiles if p.get("name")
                    ]
                    print(
                        f"Warning: no Voicebox profile named '{voice}'. "
                        f"Available: {', '.join(available) or 'none'}. "
                        f"Falling through to binding/active-voice/first profile.",
                        file=sys.stderr,
                    )
            except Exception as e:
                print(
                    f"Note: could not resolve voice name '{voice}' via /profiles ({e}); "
                    f"trying fallback sources.",
                    file=sys.stderr,
                )
        else:
            return voice

    local = _read_local_active_voice()
    if local:
        return local

    try:
        active_data = fetcher(f"{base_url}/settings/active-voice")
        if isinstance(active_data, dict):
            active_id = (
                active_data.get("voice_id")
                or active_data.get("profile_id")
                or active_data.get("active_voice_id")
                or active_data.get("id")
                or ""
            )
            active_id = str(active_id).strip()
            if active_id and active_id.lower() not in _INVALID_VOICE_IDS:
                print(
                    "Note: using Voicebox process-global active-voice; "
                    "prefer per-Hermes-profile binding via voicebox_bind.py.",
                    file=sys.stderr,
                )
                return active_id
    except Exception as e:
        print(
            f"Note: active-voice settings unavailable ({e}); using profiles list.",
            file=sys.stderr,
        )

    profiles = fetcher(f"{base_url}/profiles")
    if profiles:
        return str(profiles[0]["id"])
    raise RuntimeError("No voice profiles available.")


def _post_stream(url: str, payload: dict, timeout: int) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _post_json(url: str, payload: dict, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", errors="ignore")
        return json.loads(body) if body else {}


def _get_bytes(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _profile_sample_count(profile: dict, base_url: str | None = None, profile_id: str | None = None) -> int:
    """Voicebox list/detail often reports sample_count=0 even when /samples has rows."""
    raw = profile.get("sample_count")
    try:
        count = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        return count
    pid = profile_id or profile.get("id")
    if not base_url or not pid:
        return count
    try:
        samples = _get_json(f"{base_url}/profiles/{pid}/samples")
        if isinstance(samples, list):
            return len(samples)
        if isinstance(samples, dict):
            rows = samples.get("samples") or samples.get("items") or []
            return len(rows) if isinstance(rows, list) else count
    except Exception:
        pass
    return count


def _profile_tts_preflight(
    profile: dict,
    *,
    base_url: str | None = None,
    profile_id: str | None = None,
) -> str | None:
    """Return a human error if the profile cannot generate, else None."""
    if not profile:
        return "Profile not found."
    voice_type = str(profile.get("voice_type") or "").lower()
    sample_count = _profile_sample_count(profile, base_url=base_url, profile_id=profile_id)
    preset_voice = profile.get("preset_voice_id") or profile.get("presetVoiceId")
    name = profile.get("name") or profile.get("id") or "profile"

    if voice_type == "preset" and not preset_voice:
        return (
            f"Preset profile '{name}' has no preset_voice_id. "
            "Open Voicebox → Profiles and set a Kokoro/Qwen preset voice, "
            "or re-open the Hermes Voicebox sidebar to repair sample presets."
        )
    if voice_type in ("cloned", "custom", "") and sample_count == 0 and not preset_voice:
        return (
            f"Profile '{name}' has no reference samples (and is not a preset). "
            "Add a WAV/MP3 sample in Voicebox, or pick a Kokoro preset voice."
        )
    return None


def _model_status_hint(base_url: str, engine: str | None) -> str:
    try:
        status = _get_json(f"{base_url}/models/status")
    except Exception as e:
        return f"(could not query /models/status: {e})"

    rows = status if isinstance(status, list) else status.get("models") or status.get("items") or []
    if isinstance(status, dict) and not rows:
        # dict keyed by model name
        rows = [
            {"model_name": k, **(v if isinstance(v, dict) else {"downloaded": bool(v)})}
            for k, v in status.items()
            if isinstance(v, (dict, bool))
        ]

    eng = (engine or "").lower()
    relevant = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_eng = str(row.get("engine") or row.get("model_name") or "").lower()
        name = str(row.get("model_name") or row.get("name") or row_eng)
        if eng and eng not in row_eng and eng not in name.lower():
            continue
        downloaded = row.get("downloaded")
        if downloaded is None:
            downloaded = row.get("ready") or row.get("cached")
        relevant.append(f"{name}: downloaded={downloaded}")

    if not relevant:
        return f"(no /models/status rows matched engine={engine!r})"
    return "Model status: " + "; ".join(relevant[:6])


def _is_cuda_oom_error(err: BaseException | str) -> bool:
    """True when Voicebox/PyTorch reports GPU memory exhaustion."""
    s = str(err).lower()
    needles = (
        "out of memory",
        "cuda out of memory",
        "cudaoom",
        "cudnn_status_alloc_failed",
        "hip out of memory",
        "failed to allocate",
        "oom",
    )
    # Avoid matching unrelated "boom"/"room" — require cuda/gpu context for bare oom.
    if "out of memory" in s or "cudaoom" in s or "cudnn_status_alloc_failed" in s:
        return True
    if "failed to allocate" in s and ("cuda" in s or "gpu" in s or "vram" in s):
        return True
    if re.search(r"\boom\b", s) and ("cuda" in s or "gpu" in s or "vram" in s):
        return True
    return any(n in s for n in needles if n not in {"oom"})


def _nvidia_vram_mb() -> tuple[int | None, int | None]:
    """Return (used_miB, free_miB) from nvidia-smi, or (None, None)."""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if proc.returncode != 0:
            return None, None
        line = (proc.stdout or "").strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            return None, None
        return int(float(parts[0])), int(float(parts[1]))
    except Exception:
        return None, None


def _post_json_ok(url: str, body: dict | None = None, timeout: float = 60.0) -> tuple[bool, str]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, (resp.read().decode("utf-8", errors="ignore") or "")[:300]
    except Exception as e:
        return False, str(e)


def unload_voicebox_models(base_url: str) -> dict:
    """Best-effort unload of loaded TTS models via Voicebox public API.

    Voicebox sometimes reports unload success while VRAM stays full — callers
    should compare nvidia-smi before/after and restart Voicebox when needed.
    """
    base = base_url.rstrip("/")
    result: dict = {"unloaded": [], "errors": [], "default": None, "vram_before": None, "vram_after": None}
    used0, free0 = _nvidia_vram_mb()
    result["vram_before"] = {"used_mb": used0, "free_mb": free0}

    loaded: list[str] = []
    try:
        status = _get_json(f"{base}/models/status")
        rows = status if isinstance(status, list) else status.get("models") or status.get("engines") or []
        if isinstance(status, dict) and isinstance(status.get("models"), dict):
            rows = [
                {"model_name": k, **(v if isinstance(v, dict) else {"loaded": bool(v)})}
                for k, v in status["models"].items()
            ]
        elif isinstance(status, dict) and not rows:
            rows = [
                {"model_name": k, **(v if isinstance(v, dict) else {"loaded": bool(v)})}
                for k, v in status.items()
                if isinstance(v, (dict, bool))
            ]
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not row.get("loaded"):
                continue
            name = row.get("model_name") or row.get("name") or row.get("engine")
            if name:
                loaded.append(str(name))
    except Exception as e:
        result["errors"].append({"list": str(e)})

    for name in loaded:
        ok, detail = _post_json_ok(f"{base}/models/{urllib.parse.quote(name, safe='')}/unload")
        if ok:
            result["unloaded"].append(name)
        else:
            ok2, detail2 = _post_json_ok(f"{base}/models/unload", body={"model_name": name})
            if ok2:
                result["unloaded"].append(name)
            else:
                result["errors"].append({"model": name, "error": detail or detail2})

    ok, detail = _post_json_ok(f"{base}/models/unload")
    result["default"] = {"ok": ok, "detail": detail}

    used1, free1 = _nvidia_vram_mb()
    result["vram_after"] = {"used_mb": used1, "free_mb": free1}
    return result


def _gpu_recovery_hint(base_url: str, unload_result: dict | None = None) -> str:
    used, free = _nvidia_vram_mb()
    lines = [
        "Hint: CUDA/GPU memory pressure. Prefer Qwen model_size=0.6B "
        "(default), use the Desktop plugin Free GPU button, or:",
        f"  python3 ~/.hermes/scripts/voicebox_gpu.py unload",
        f"  systemctl --user restart voicebox.service   # when unload leaves VRAM full",
        f"  curl -s {base_url.rstrip('/')}/health",
    ]
    if used is not None:
        lines.append(f"  nvidia-smi VRAM now: used={used} MiB free={free} MiB")
    if unload_result is not None:
        before = (unload_result.get("vram_before") or {}).get("used_mb")
        after = (unload_result.get("vram_after") or {}).get("used_mb")
        if before is not None and after is not None and after >= before - 64:
            lines.append(
                "  Note: unload API ran but VRAM did not drop meaningfully — "
                "restart Voicebox (systemctl --user restart voicebox.service)."
            )
    return "\n".join(lines)


def _generate_via_async_job(base_url: str, payload: dict, timeout: int) -> bytes:
    """Fallback when /generate/stream 500s: queue /generate then fetch /audio/{id}."""
    job = _post_json(f"{base_url}/generate", payload, timeout=min(60, timeout))
    gen_id = job.get("id")
    if not gen_id:
        raise RuntimeError(f"/generate returned no id: {job}")

    deadline = time.time() + timeout
    last_status = job.get("status") or "generating"
    last_error = job.get("error")
    while time.time() < deadline:
        try:
            st = _get_json(f"{base_url}/history/{gen_id}")
            last_status = st.get("status") or last_status
            last_error = st.get("error") or last_error
            if last_status == "completed":
                return _get_bytes(f"{base_url}/audio/{gen_id}", timeout=min(60, timeout))
            if last_status == "failed":
                raise RuntimeError(last_error or "generation failed")
        except RuntimeError:
            raise
        except Exception:
            pass
        time.sleep(1.0)

    raise TimeoutError(
        f"Timed out waiting for generation {gen_id} (last status={last_status}, error={last_error})"
    )


def generate_wav(base_url: str, payload: dict, timeout: int) -> bytes:
    """Prefer streaming WAV; fall back to async job API on server 500."""
    try:
        return _post_stream(f"{base_url}/generate/stream", payload, timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        if e.code != 500:
            raise RuntimeError(f"HTTP {e.code}: {body or e.reason}") from e
        print(
            f"Stream generate HTTP 500 ({body or 'Internal Server Error'}); "
            "retrying via /generate job API...",
            file=sys.stderr,
        )
        try:
            return _generate_via_async_job(base_url, payload, timeout)
        except Exception as fallback_err:
            raise RuntimeError(
                f"HTTP 500 on /generate/stream: {body or 'Internal Server Error'}; "
                f"job fallback also failed: {fallback_err}"
            ) from fallback_err


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _touch_tts_activity() -> None:
    """Stamp last TTS time so idle GPU unload knows when to free VRAM."""
    try:
        home = _hermes_home()
        os.makedirs(home, exist_ok=True)
        path = os.path.join(home, "voicebox_tts_activity")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"touched_at": time.time(), "pid": os.getpid()}, f)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Hermes-Voicebox TTS Bridge")
    parser.add_argument("--text-file", "-t", required=True, help="Path to temp text file")
    parser.add_argument("--out",       "-o", required=True, help="Path to write output audio file")
    parser.add_argument("--voice",     "-v", help="Voice profile ID")
    parser.add_argument("--base-url",  "-b", default=None, help="Base URL of Voicebox API (e.g. http://127.0.0.1:17493)")
    parser.add_argument(
        "--provider",
        choices=["voicebox", "fish"],
        default="voicebox",
        help="TTS backend. 'fish' routes to hosted Fish Audio (needs FISH_KEY).",
    )
    parser.add_argument("--fish-key",   default=None, help="Fish Audio API key (else FISH_KEY env)")
    parser.add_argument(
        "--fish-voice",
        default=None,
        help="Fish Audio voice id. If omitted, uses cached clone for --fish-label or Fish default.",
    )
    parser.add_argument(
        "--fish-label",
        default="jarvis",
        help="Cached-clone label to resolve a voice id from (~/.hermes/fish_voices.json).",
    )
    args = parser.parse_args()

    base_url = get_base_url(args.base_url)

    # 0. Read text
    try:
        with open(args.text_file, "r", encoding="utf-8") as f:
            text = f.read().strip()
    except Exception as e:
        print(f"Error reading text file: {e}", file=sys.stderr)
        sys.exit(1)

    if not text:
        # Hermes expects an output file; write a minimal valid silent WAV.
        print("Warning: Empty text, writing silent WAV.", file=sys.stderr)
        silent = _build_wav([b"\x00\x00" * 240], 24000, 1, 16)
        try:
            with open(args.out, "wb") as f:
                f.write(silent)
        except Exception as e:
            print(f"Error writing silent output file: {e}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    # 0b. Hosted Fish Audio provider — no local Voicebox service required.
    if args.provider == "fish":
        if not _HAVE_FISH:
            print(
                "Error: fish_tts.py not importable; install it alongside this bridge.",
                file=sys.stderr,
            )
            sys.exit(1)
        voice_id = args.fish_voice or _fish_cached_voice(args.fish_label)
        if not voice_id:
            print("Warning: no cached Fish clone for "
                  f"--fish-label {args.fish_label!r}; using Fish default voice.",
                  file=sys.stderr)
        else:
            print(f"Fish: using cloned voice id {voice_id} "
                  f"(label {args.fish_label!r}).", file=sys.stderr)
        try:
            _fish_synthesize(
                text,
                voice_id=voice_id,
                api_key=args.fish_key,
                out_path=args.out,
                audio_format="wav",
                sample_rate=44100,
                timeout=120,
            )
        except Exception as e:
            print(f"Error: Fish TTS failed: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Done — Fish Audio output written to {args.out}", file=sys.stderr)
        sys.exit(0)

    # 1. Ensure Voicebox service is running (fail closed if still unreachable)
    def _ensure_service_running():
        last_err = None
        for _ in range(15):
            try:
                _get_json(f"{base_url}/health")
                return
            except Exception as e:
                last_err = e
                # Auto-start via systemd is Linux-only; Windows users start Voicebox manually.
                if sys.platform.startswith("linux"):
                    try:
                        subprocess.run(
                            ["systemctl", "--user", "start", "voicebox"],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    except Exception:
                        pass
                time.sleep(1)
        print(
            f"Error: Voicebox API unreachable at {base_url}/health ({last_err}). "
            "Start the backend and retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    _ensure_service_running()

    # 2b. Resolve profile ID (CLI / local sidecar / optional API / first profile)
    try:
        profile_id = resolve_profile_id(args.voice, base_url)
    except Exception as e:
        print(f"Error resolving fallback profile: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Resolve engine + language from profile metadata
    engine = None
    model_size = None
    language = "en"
    profile = {}
    try:
        profile = _get_json(f"{base_url}/profiles/{profile_id}")
        raw_engine = profile.get("default_engine") or profile.get("preset_engine")
        engine = normalize_engine(raw_engine)
        model_size = model_size_for_engine(raw_engine)
        language = profile.get("language") or language
        live_samples = _profile_sample_count(
            profile, base_url=base_url, profile_id=profile_id
        )
        eng_note = (
            f" (remapped from {raw_engine!r})"
            if raw_engine and engine and str(raw_engine) != str(engine)
            else ""
        )
        print(
            f"Profile '{profile.get('name') or profile_id}' "
            f"type={profile.get('voice_type')!r} engine={engine!r}{eng_note} "
            f"model_size={model_size!r} "
            f"preset_voice_id={profile.get('preset_voice_id')!r} "
            f"samples={live_samples!r}",
            file=sys.stderr,
        )
        preflight = _profile_tts_preflight(
            profile, base_url=base_url, profile_id=profile_id
        )
        if preflight:
            print(f"Error: {preflight}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Warning: could not fetch profile details ({e}), engine left unset.", file=sys.stderr)

    # Strip stage directions / emphasis markup Qwen would otherwise speak aloud
    # (e.g. [sarcastically], [whiny voice], (sighs dramatically)).
    cleaned = sanitize_spoken_text_for_voicebox(text, engine)
    if cleaned != text:
        print(
            f"Note: stripped stage-direction/emphasis markup for TTS "
            f"({len(text)} → {len(cleaned)} chars).",
            file=sys.stderr,
        )
        text = cleaned
    if not text.strip():
        print("Error: text empty after stripping stage-direction markup.", file=sys.stderr)
        sys.exit(1)

    # 4. Split text into chunks (sentence-isolated for clone engines)
    chunks = split_tts_chunks(text, engine)
    n = len(chunks)
    print(
        f"Generating TTS for profile '{profile_id}' in {n} chunk(s)"
        f"{' (sentence mode)' if _engine_prefers_sentence_chunks(engine) else ''}...",
        file=sys.stderr,
    )

    # 5. Generate each chunk
    wav_format = None   # (sample_rate, num_channels, bits_per_sample)
    pcm_segments = []

    # Stamp before synthesis so idle-unload cannot race a long first request
    # after the idle threshold (daemon polls ~every 10s).
    _touch_tts_activity()

    for i, chunk_text in enumerate(chunks, 1):
        print(f"  Chunk {i}/{n} ({len(chunk_text)} chars)...", file=sys.stderr)
        # Refresh during multi-chunk / slow jobs so mid-request idle unload
        # cannot fire while we are still synthesizing.
        if i > 1:
            _touch_tts_activity()
        payload = {
            "profile_id": profile_id,
            "text": chunk_text,
            "language": language,
            # Keep Voicebox's internal splitter aligned with our chunk size.
            "max_chunk_chars": max(len(chunk_text), 100),
        }
        if engine:
            payload["engine"] = engine
        if model_size:
            payload["model_size"] = model_size

        raw = None
        last_err = None
        oom_retried = False
        for attempt in range(3):
            try:
                raw = generate_wav(base_url, payload, CHUNK_TIMEOUT)
                break
            except urllib.error.URLError as e:
                last_err = e.reason
                if attempt == 0:
                    print(f"Connection error on chunk {i} (retrying): {e.reason}", file=sys.stderr)
                    time.sleep(1)
                    continue
                print(f"Connection error on chunk {i}: {e.reason}", file=sys.stderr)
                sys.exit(1)
            except Exception as e:
                print(f"TTS failed on chunk {i}: {e}", file=sys.stderr)
                print(_model_status_hint(base_url, engine), file=sys.stderr)
                err_l = str(e).lower()
                if _is_cuda_oom_error(e) and not oom_retried:
                    oom_retried = True
                    # Force the small Qwen checkpoint on retry when applicable.
                    if engine and str(engine).lower() in {"qwen", "qwen3"}:
                        payload["model_size"] = "0.6B"
                        model_size = "0.6B"
                    print(
                        "CUDA OOM detected — unloading Voicebox models and retrying "
                        f"chunk {i} at model_size={payload.get('model_size')!r}...",
                        file=sys.stderr,
                    )
                    unload_info = unload_voicebox_models(base_url)
                    print(f"Unload result: {json.dumps(unload_info)}", file=sys.stderr)
                    print(_gpu_recovery_hint(base_url, unload_info), file=sys.stderr)
                    time.sleep(2)
                    continue
                if "cuda" in err_l or "unspecified launch failure" in err_l or _is_cuda_oom_error(e):
                    print(_gpu_recovery_hint(base_url), file=sys.stderr)
                    print(
                        "Also check `nvidia-smi` for ERR! (wedged driver after suspend); "
                        "CPU fallback: restart Voicebox with CUDA_VISIBLE_DEVICES=.",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "Hint: confirm Voicebox can speak this profile in its own UI, "
                        "and that the engine model is downloaded "
                        f"(curl -s {base_url}/models/status).",
                        file=sys.stderr,
                    )
                sys.exit(1)

        if raw is None:
            print(f"Connection error on chunk {i}: {last_err}", file=sys.stderr)
            sys.exit(1)

        # Parse WAV header to extract PCM
        try:
            sr, nc, bps, data_offset, data_len = _parse_wav_header(raw)
            chunk_fmt = (sr, nc, bps)
            if wav_format is None:
                wav_format = chunk_fmt
            elif chunk_fmt != wav_format:
                print(
                    f"Error: chunk {i} WAV format {chunk_fmt} does not match "
                    f"first chunk format {wav_format}.",
                    file=sys.stderr,
                )
                sys.exit(1)
            pcm_segments.append(raw[data_offset: data_offset + data_len])
        except ValueError:
            # Backend returned an error JSON instead of WAV — surface it
            print(f"Invalid WAV on chunk {i}: {raw[:200]}", file=sys.stderr)
            sys.exit(1)

    # 6. Concatenate and write output
    if not pcm_segments:
        print("Error: No audio generated.", file=sys.stderr)
        sys.exit(1)

    sr, nc, bps = wav_format
    output_wav  = _build_wav(pcm_segments, sr, nc, bps)

    try:
        with open(args.out, "wb") as f:
            f.write(output_wav)
        total_kb = len(output_wav) // 1024
        print(f"Done — {total_kb}K WAV written to {args.out}", file=sys.stderr)
        _touch_tts_activity()
    except Exception as e:
        print(f"Error writing output file: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
