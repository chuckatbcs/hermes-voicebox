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


def resolve_profile_id(cli_voice: str, base_url: str, get_json=None) -> str:
    """
    Resolve which Voicebox profile to use.

    Precedence:
      1) CLI --voice (Hermes substitutes tts.providers.voicebox.voice here)
      2) Per-profile binding / sidecar under HERMES_HOME
      3) Voicebox /settings/active-voice (demoted — process-global, cross-profile bleed)
      4) First Voicebox profile
    """
    fetcher = get_json or _get_json
    voice = (cli_voice or "").strip()
    if voice and voice.lower() not in _INVALID_VOICE_IDS:
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
    args = parser.parse_args()

    base_url = get_base_url(args.base_url)

    # 1. Read text
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

    # 2. Ensure Voicebox service is running (fail closed if still unreachable)
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
    language = "en"
    profile = {}
    try:
        profile = _get_json(f"{base_url}/profiles/{profile_id}")
        engine = profile.get("default_engine") or profile.get("preset_engine")
        language = profile.get("language") or language
        live_samples = _profile_sample_count(
            profile, base_url=base_url, profile_id=profile_id
        )
        print(
            f"Profile '{profile.get('name') or profile_id}' "
            f"type={profile.get('voice_type')!r} engine={engine!r} "
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

        raw = None
        last_err = None
        for attempt in range(2):
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
                if "cuda" in err_l or "unspecified launch failure" in err_l:
                    print(
                        "Hint: Voicebox hit a CUDA/GPU failure (often a wedged driver "
                        "after suspend). Check `nvidia-smi` for ERR!, restart the "
                        "Voicebox service with CUDA_VISIBLE_DEVICES= for CPU TTS, "
                        "or reboot to clear the GPU, then retry.",
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
