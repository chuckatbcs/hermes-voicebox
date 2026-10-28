"""Hermes speak-stream adapter for the Voicebox command TTS provider.

Desktop read-aloud prefers ``/api/audio/speak-stream``. Without a registered
streamer, Hermes falls back to one-shot ``/api/audio/speak`` for the *entire*
reply — so cloned Chatterbox voices wait until every sentence is synthesized
(and often hit the 120s command timeout).

This module registers ``voicebox`` with Hermes' streaming registry and provides
a **look-ahead PCM pipeline**: while sentence N's audio is handed to the
WebSocket/player, sentence N+1 is already synthesizing so playback stays
continuous.

Installed into ``$HERMES_HOME/hermes-agent/tools/voicebox_command_streamer.py``
and imported from ``tools.tts_streaming`` via a marked block. The installer also
hooks ``hermes_cli/web_server.py`` speak-stream to use ``produce_speak_stream_pcm``.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Iterator, Optional


def _resolve_engine_and_model_size(base_url: str, profile_id: Optional[str], engine: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Fetch profile metadata to resolve engine + model_size, matching voicebox_tts.py logic.

    Returns (engine, model_size). If profile fetch fails, returns (engine, None)
    — the caller can still fire preload without model_size and Voicebox will
    use the engine's default.
    """
    if engine:
        # Engine was explicitly configured — just compute model_size
        return engine, _compute_model_size(engine)

    try:
        import json
        import urllib.request
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/profiles/{profile_id}", timeout=3) as resp:
            profile = json.loads(resp.read())
        raw_engine = profile.get("default_engine") or profile.get("preset_engine")
        if raw_engine:
            engine = _normalize_engine(raw_engine)
            size = _compute_model_size(raw_engine)
            return engine, size
    except Exception:
        pass

    return None, None


def _normalize_engine(raw: str | None) -> Optional[str]:
    if not raw:
        return None
    return str(raw).strip().lower()


def _compute_model_size(engine: str | None) -> Optional[str]:
    """Mirror voicebox_tts.model_size_for_engine — prefer 0.6B on 8GB-class GPUs."""
    if engine is None:
        return None
    raw = str(engine).strip().lower()
    override = (os.environ.get("VOICEBOX_QWEN_MODEL_SIZE") or "").strip()
    if raw in {"qwen", "qwen_fast", "qwen3"}:
        if override in {"0.6B", "1.7B", "1B", "3B"}:
            return override
        if raw == "qwen_fast":
            return "0.6B"
        return "0.6B"
    return None


def preload_voicebox_model(
    base_url: str = "http://127.0.0.1:17493",
    profile_id: Optional[str] = None,
    engine: Optional[str] = None,
) -> None:
    """Fire-and-forget async model warm-up.

    Hits Voicebox' ``POST /models/preload`` so the ~10s cold model load
    overlaps the assistant's text generation instead of blocking the first
    spoken sentence. Non-blocking: returns immediately; the load runs on the
    Voicebox server. ``touch_last_active`` is called server-side, which also
    keeps the model resident through the session (idle unload is ~10 min).

    When ``engine`` is None, fetches the profile from Voicebox to resolve the
    engine + model_size (e.g. qwen → 0.6B for Cartman on 8GB GPU). This ensures
    the preloader and the actual TTS use the **same** model, avoiding a wasteful
    unload→reload cycle when Voicebox's default size differs from the OOM-safe
    size voicebox_tts.py selects.
    """
    import json
    import urllib.request

    # Resolve engine + model_size from profile metadata if needed.
    resolved_engine, resolved_size = _resolve_engine_and_model_size(base_url, profile_id, engine)
    if resolved_engine:
        engine = resolved_engine

    payload: dict = {"profile_id": profile_id}
    if engine:
        payload["engine"] = engine
    if resolved_size:
        payload["model_size"] = resolved_size
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/models/preload",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as exc:  # never block speech on a preload hiccup
        logger.debug("Voicebox preload skipped: %s", exc)


def _resolve_voicebox_profile(base_url: str, profile_id: Optional[str]) -> tuple:
    return profile_id, "chatterbox_turbo"


from tools.tts_streaming import StreamingTTSProvider, register

logger = logging.getLogger(__name__)

# First emitted piece is kept small so audio starts almost immediately;
# later pieces grow to full sentence size. The number of words in the
# lead chunk is a latency/continuity trade-off: small = fast first sound,
# large = fewer synthesis hand-offs. 10-14 words renders in ~1s on a
# laptop GPU while keeping the gap before sentence 2 short.
ADAPTIVE_FIRST_CHUNK_WORDS = 12


def _adaptive_first_split(text: str) -> Iterator[str]:
    """Yield a small lead chunk, then the remainder.

    Used for the very first piece so playback can begin before the whole
    first sentence is synthesized. Subsequent pieces are emitted whole.
    """
    toks = text.split()
    if len(toks) <= ADAPTIVE_FIRST_CHUNK_WORDS:
        yield text
        return
    yield " ".join(toks[:ADAPTIVE_FIRST_CHUNK_WORDS])
    yield " ".join(toks[ADAPTIVE_FIRST_CHUNK_WORDS:])


def _provider_section(tts_config: Dict, section: Dict) -> Dict:
    """Prefer ``tts.providers.<name>`` (command provider) over ``tts.<name>``."""
    providers = tts_config.get("providers") if isinstance(tts_config, dict) else None
    if isinstance(providers, dict):
        named = providers.get("fish") if "fish" in (providers or {}) else None
        if isinstance(named, dict) and named:
            return named
    return section if isinstance(section, dict) else {}


def _fish_tts_module():
    """Import the deployed fish_tts helper (all-homes cache/key scanning)."""
    import sys

    for cand in (
        os.path.join(os.path.expanduser("~"), ".hermes", "scripts"),
        os.path.join(os.environ.get("HERMES_HOME", ""), "scripts"),
    ):
        if cand and cand not in sys.path and os.path.isdir(cand):
            sys.path.insert(0, cand)
    import fish_tts

    return fish_tts


@register("voicebox")
class VoiceboxCommandStreamer(StreamingTTSProvider):
    """Sentence → command TTS WAV → int16 mono PCM (Desktop speak-stream)."""

    sample_rate = 24000
    channels = 1

    def __init__(self, tts_config: Dict, section: Dict):
        resolved = _provider_section(tts_config, section)
        super().__init__(tts_config, resolved)
        # Capture the bound Voicebox profile + base URL once, so a speak can
        # trigger a model warm-up with zero network lookups on the hot path.
        self._vb_voice = (resolved or {}).get("voice")
        self._vb_base = (resolved or {}).get("base_url") or "http://127.0.0.1:17493"
        self._vb_engine = (resolved or {}).get("engine")
        self._vb_preloaded = False
        # Fire preload immediately at construction time (WebSocket connect),
        # not at first stream() call. This overlaps the ~10s cold model load
        # with whatever the user is doing before the first text token arrives,
        # shaving the perceived latency for first-audio to near-zero.
        # Pass engine=None so Voicebox resolves the engine from the profile's
        # default_engine (e.g. "qwen" → 0.6B for Cartman, not chatterbox_turbo).
        threading.Thread(
            target=preload_voicebox_model,
            kwargs={"base_url": self._vb_base, "profile_id": self._vb_voice, "engine": self._vb_engine},
            daemon=True,
            name="vb-preload-init",
        ).start()
        self._vb_preloaded = True

    @staticmethod
    def available() -> bool:
        # Defer real config checks to resolve/init; registration is enough to
        # opt command-Voicebox into speak-stream instead of whole-reply POST.
        return True

    def stream(self, text: str) -> Iterator[bytes]:
        cleaned = (text or "").strip()
        if not cleaned:
            return

        # Preload already fired in __init__ (at WebSocket connect time, earlier
        # than this first stream() call). The guard is kept for safety — if
        # stream() is called directly (not via speak-stream), preload here.
        if not self._vb_preloaded:
            self._vb_preloaded = True
            threading.Thread(
                target=preload_voicebox_model,
                kwargs={"base_url": self._vb_base, "profile_id": self._vb_voice, "engine": self._vb_engine},
                daemon=True,
                name="vb-preload",
            ).start()

        from tools.tts_tool import text_to_speech_tool

        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            text_to_speech_tool(text=cleaned, output_path=path)
            if not os.path.isfile(path) or os.path.getsize(path) <= 44:
                raise RuntimeError("Voicebox command TTS produced empty audio")
            # One (or few large) blobs so the producer can start the next
            # sentence immediately instead of pacing on tiny WS frames.
            yield from _wav_file_to_pcm_chunks(
                path, expect_rate=self.sample_rate, frames=256_000
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


@register("fish")
class FishStreamer(StreamingTTSProvider):
    """Fish Audio (hosted) → sentence-chunked int16 mono PCM.

    Fish has no chunked streaming API, but the speak-stream producer calls
    ``stream()`` per sentence with one-sentence look-ahead, so each sentence
    is synthesized while the previous one plays — the same latency win as
    the local Voicebox path, without waiting for the whole reply.
    """

    sample_rate = 24000
    channels = 1

    # Fish returns mp3/wav; we request wav at a rate we can feed directly.
    _FISH_SAMPLE_RATE = 44100

    def __init__(self, tts_config: Dict, section: Dict):
        resolved = _provider_section(tts_config, section)
        super().__init__(tts_config, resolved)
        self._voice_id = (resolved or {}).get("voice")
        self._label = (resolved or {}).get("provider_label") or "fish"
        clones = (resolved or {}).get("clones") or {}
        # voice may be a label ("jarvis") that maps to an id via clones,
        # or already a 32-hex Fish model id.
        v = self._voice_id
        if v and clones.get(str(v)) and not (len(str(v)) == 32 and all(c in "0123456789abcdef" for c in str(v).lower())):
            self._voice_id = str(clones[str(v)])

    @staticmethod
    def available() -> bool:
        try:
            return bool(_fish_tts_module().load_fish_key())
        except Exception:
            return False

    def stream(self, text: str) -> Iterator[bytes]:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        ft = _fish_tts_module()
        key = ft.load_fish_key()
        if not key:
            raise RuntimeError("Fish Audio key missing")
        audio = ft.synthesize(
            text=cleaned,
            voice_id=self._voice_id,
            api_key=key,
            audio_format="wav",
            sample_rate=self._FISH_SAMPLE_RATE,
        )
        import io

        import wave as _wave

        with _wave.open(io.BytesIO(audio), "rb") as wf:
            rate = wf.getframerate()
            width = wf.getsampwidth()
            channels = wf.getnchannels()
            raw = wf.readframes(wf.getnframes())
        if width != 2:
            raise RuntimeError(f"Unsupported Fish WAV sample width: {width}")
        pcm = raw
        if channels == 2:
            pcm = _stereo_int16_to_mono(raw)
        elif channels != 1:
            raise RuntimeError(f"Unsupported Fish WAV channels: {channels}")
        if rate != self.sample_rate:
            pcm = _resample_int16(pcm, rate, self.sample_rate)
        # One blob per sentence — the producer starts the next request
        # immediately instead of pacing on tiny frames.
        yield pcm


def _resample_int16(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resampler for int16 mono PCM (stdlib only)."""
    import array

    samples = array.array("h")
    samples.frombytes(data)
    n = len(samples)
    if n == 0 or src_rate == dst_rate:
        return data
    out_len = round(n * dst_rate / src_rate)
    out = array.array("h", bytes(2 * out_len))
    step = (n - 1) / max(out_len - 1, 1)
    for i in range(out_len):
        pos = i * step
        i0 = int(pos)
        frac = pos - i0
        i1 = min(i0 + 1, n - 1)
        out[i] = int(samples[i0] * (1 - frac) + samples[i1] * frac)
    return out.tobytes()


def synthesize_pcm_bytes(streamer: StreamingTTSProvider, text: str) -> bytes:
    """Fully synthesize one piece to a single PCM buffer."""
    return b"".join(streamer.stream(text) or ())


def produce_speak_stream_pcm(
    *,
    sentences_fn: Callable[[], Iterator[str]],
    streamer: StreamingTTSProvider,
    cap: int,
    stop,
    emit: Callable[[bytes], None],
    strip_md: Callable[[str], str],
    split_fn: Callable[[str, int], list],
) -> None:
    """
    Look-ahead speak-stream producer.

    Pattern: finish synth(N) → start synth(N+1) → emit PCM(N).
    Emitting is fast (queued to the WS loop), so synth(N+1) overlaps playback
    of N and removes the post-sentence silence users hear with strict
    synth→send→synth sequencing under WS backpressure.
    """

    def pieces() -> Iterator[str]:
        first = True
        for sentence in sentences_fn():
            if getattr(stop, "is_set", lambda: False)():
                return
            cleaned = (strip_md(sentence) or "").strip()
            if not cleaned:
                continue
            if first:
                # Fast first sound: emit a small lead chunk, then the rest.
                yield from _adaptive_first_split(cleaned)
                first = False
                continue
            for piece in split_fn(cleaned, cap):
                piece = (piece or "").strip()
                if piece:
                    yield piece

    it = pieces()
    try:
        first = next(it)
    except StopIteration:
        return

    # Single worker: Voicebox/command TTS is typically one-model-at-a-time.
    # Look-ahead of one is enough to hide synth latency behind playback.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="vb-tts") as pool:
        pending = pool.submit(synthesize_pcm_bytes, streamer, first)
        for nxt in it:
            if getattr(stop, "is_set", lambda: False)():
                pending.cancel()
                return
            try:
                pcm = pending.result()
            except Exception:
                logger.exception("speak-stream look-ahead synth failed")
                pending = pool.submit(synthesize_pcm_bytes, streamer, nxt)
                continue
            # Start next synthesis BEFORE emitting so WS/send backpressure
            # cannot delay the next Voicebox job.
            pending = pool.submit(synthesize_pcm_bytes, streamer, nxt)
            if pcm and not getattr(stop, "is_set", lambda: False)():
                emit(pcm)

        if getattr(stop, "is_set", lambda: False)():
            pending.cancel()
            return
        try:
            pcm = pending.result()
        except Exception:
            logger.exception("speak-stream final synth failed")
            return
        if pcm:
            emit(pcm)


def _wav_file_to_pcm_chunks(
    path: str, expect_rate: int = 24000, frames: int = 256_000
) -> Iterator[bytes]:
    """Yield raw PCM frames from a WAV file (mono int16 preferred)."""
    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        if rate != expect_rate:
            logger.warning(
                "Voicebox WAV sample rate %s != speak-stream rate %s; "
                "playback speed may be off",
                rate,
                expect_rate,
            )
        if width != 2:
            raise RuntimeError(f"Unsupported WAV sample width: {width} (want 16-bit)")
        while True:
            data = wf.readframes(frames)
            if not data:
                break
            if channels == 1:
                yield data
            elif channels == 2:
                yield _stereo_int16_to_mono(data)
            else:
                raise RuntimeError(f"Unsupported WAV channels: {channels}")


def _stereo_int16_to_mono(data: bytes) -> bytes:
    import array

    samples = array.array("h")
    samples.frombytes(data)
    if len(samples) % 2:
        samples.append(0)
    mono = array.array("h")
    for i in range(0, len(samples), 2):
        mono.append((samples[i] + samples[i + 1]) // 2)
    return mono.tobytes()
