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
# Maximum characters per chunk sent to the backend.
# The backend itself also splits at 800 chars, but we stay well under that
# so we control the boundaries at sentence level.
MAX_CHARS       = 600


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


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_json(url: str) -> dict:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def _post_stream(url: str, payload: dict, timeout: int) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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

    # 2b. Resolve profile ID
    profile_id = args.voice
    if not profile_id or profile_id in ("default", "undefined", "", "00000000-0000-0000-0000-000000000000"):
        try:
            # Try to get the active voice from the backend
            active_data = _get_json(f"{base_url}/settings/active-voice")
            active_id = active_data.get("voice_id")
            
            if active_id:
                profile_id = active_id
            else:
                # Fallback to the first available profile
                profiles = _get_json(f"{base_url}/profiles")
                if profiles:
                    profile_id = profiles[0]["id"]
                else:
                    print("Error: No voice profiles available.", file=sys.stderr)
                    sys.exit(1)
        except Exception as e:
            print(f"Error resolving fallback profile: {e}", file=sys.stderr)
            sys.exit(1)

    # 3. Resolve engine + language from profile metadata
    engine = None
    language = "en"
    try:
        profile = _get_json(f"{base_url}/profiles/{profile_id}")
        engine  = profile.get("default_engine") or profile.get("preset_engine")
        language = profile.get("language") or language
    except Exception as e:
        print(f"Warning: could not fetch profile details ({e}), engine left unset.", file=sys.stderr)

    # 4. Split text into chunks
    chunks = _split_sentences(text, MAX_CHARS)
    n      = len(chunks)
    print(f"Generating TTS for profile '{profile_id}' in {n} chunk(s)...", file=sys.stderr)

    # 5. Generate each chunk
    wav_format   = None   # (sample_rate, num_channels, bits_per_sample)
    pcm_segments = []

    for i, chunk_text in enumerate(chunks, 1):
        print(f"  Chunk {i}/{n} ({len(chunk_text)} chars)...", file=sys.stderr)
        payload = {"profile_id": profile_id, "text": chunk_text, "language": language}
        if engine:
            payload["engine"] = engine

        raw = None
        last_err = None
        for attempt in range(2):
            try:
                raw = _post_stream(f"{base_url}/generate/stream", payload, CHUNK_TIMEOUT)
                break
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", errors="ignore")
                print(f"HTTP {e.code} on chunk {i}: {msg}", file=sys.stderr)
                sys.exit(1)
            except urllib.error.URLError as e:
                last_err = e.reason
                if attempt == 0:
                    print(f"Connection error on chunk {i} (retrying): {e.reason}", file=sys.stderr)
                    time.sleep(1)
                    continue
                print(f"Connection error on chunk {i}: {e.reason}", file=sys.stderr)
                sys.exit(1)
            except Exception as e:
                print(f"Unexpected error on chunk {i}: {e}", file=sys.stderr)
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
    except Exception as e:
        print(f"Error writing output file: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
