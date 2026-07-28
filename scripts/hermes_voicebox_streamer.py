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
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Iterator, Optional

from tools.tts_streaming import StreamingTTSProvider, register

logger = logging.getLogger(__name__)


def _provider_section(tts_config: Dict, section: Dict) -> Dict:
    """Prefer ``tts.providers.voicebox`` (command provider) over ``tts.voicebox``."""
    providers = tts_config.get("providers") if isinstance(tts_config, dict) else None
    if isinstance(providers, dict):
        named = providers.get("voicebox")
        if isinstance(named, dict) and named:
            return named
    return section if isinstance(section, dict) else {}


@register("voicebox")
class VoiceboxCommandStreamer(StreamingTTSProvider):
    """Sentence → command TTS WAV → int16 mono PCM (Desktop speak-stream)."""

    sample_rate = 24000
    channels = 1

    def __init__(self, tts_config: Dict, section: Dict):
        resolved = _provider_section(tts_config, section)
        super().__init__(tts_config, resolved)

    @staticmethod
    def available() -> bool:
        # Defer real config checks to resolve/init; registration is enough to
        # opt command-Voicebox into speak-stream instead of whole-reply POST.
        return True

    def stream(self, text: str) -> Iterator[bytes]:
        cleaned = (text or "").strip()
        if not cleaned:
            return

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
        for sentence in sentences_fn():
            if getattr(stop, "is_set", lambda: False)():
                return
            cleaned = (strip_md(sentence) or "").strip()
            if not cleaned:
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
