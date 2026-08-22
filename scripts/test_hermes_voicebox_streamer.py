#!/usr/bin/env python3
"""Unit tests for the speak-stream streamer module (voicebox + fish providers).

These run offline: no Voicebox server and no network access is required.
"""
import io
import os
import sys
import unittest
import wave
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hermes_voicebox_streamer as s  # noqa: E402


def _wav_bytes(pcm: bytes, sample_rate=24000, channels=1, bits=16) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(bits // 8)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


class TestResampleInt16(unittest.TestCase):
    def test_identity_when_rates_match(self):
        data = b"\x01\x02\x03\x04"
        self.assertEqual(s._resample_int16(data, 24000, 24000), data)

    def test_output_length_scales_with_ratio(self):
        pcm = b"".join(bytes([0x00, i]) for i in range(100))  # 100 int16 samples
        out = s._resample_int16(pcm, 44100, 22050)
        self.assertEqual(len(out), len(pcm) // 2)  # halved rate -> half samples

    def test_dc_signal_preserved(self):
        # Constant amplitude must stay constant through resampling.
        samples = [500] * 64
        pcm = b"".join(v.to_bytes(2, "little", signed=True) for v in samples)
        out = s._resample_int16(pcm, 44100, 24000)
        vals = [int.from_bytes(out[i : i + 2], "little", signed=True) for i in range(0, len(out), 2)]
        self.assertTrue(all(abs(v - 500) <= 1 for v in vals))

    def test_empty_input(self):
        self.assertEqual(s._resample_int16(b"", 44100, 24000), b"")


class TestStereoToMono(unittest.TestCase):
    def test_averages_channels(self):
        left = 100
        right = 200
        pcm = (left.to_bytes(2, "little", signed=True) + right.to_bytes(2, "little", signed=True))
        out = s._stereo_int16_to_mono(pcm)
        val = int.from_bytes(out, "little", signed=True)
        self.assertEqual(val, (left + right) // 2)


class TestProviderSection(unittest.TestCase):
    def test_prefers_providers_fish_block(self):
        tts_config = {"providers": {"fish": {"voice": "jarvis"}}, "fish": {"voice": "other"}}
        resolved = s._provider_section(tts_config, tts_config.get("fish", {}))
        self.assertEqual(resolved["voice"], "jarvis")

    def test_falls_back_to_section(self):
        self.assertEqual(s._provider_section({}, {"a": 1}), {"a": 1})
        self.assertEqual(s._provider_section(None, None), {})


class TestFishVoiceIdResolution(unittest.TestCase):
    """The label->clone-UUID mapping must never fall back to Fish's default voice."""

    def _streamer(self, section):
        with mock.patch.object(s.StreamingTTSProvider, "__init__", lambda self, c, s: None):
            fs = s.FishStreamer({}, section)
        return fs

    def test_label_mapped_through_clones(self):
        fs = self._streamer({"voice": "jarvis", "clones": {"jarvis": "a" * 32}})
        self.assertEqual(fs._voice_id, "a" * 32)

    def test_uuid_passthrough_even_if_also_in_clones(self):
        fs = self._streamer({"voice": "b" * 32, "clones": {("b" * 32): "a" * 32}})
        self.assertEqual(fs._voice_id, "b" * 32)

    def test_no_voice_stays_none(self):
        fs = self._streamer({})
        self.assertIsNone(fs._voice_id)


class TestAdaptiveFirstSplit(unittest.TestCase):
    def test_short_text_single_piece(self):
        self.assertEqual(list(s._adaptive_first_split("Hello world.")), ["Hello world."])

    def test_long_text_two_pieces(self):
        text = " ".join(f"w{i}" for i in range(40))
        pieces = list(s._adaptive_first_split(text))
        self.assertEqual(len(pieces), 2)
        self.assertEqual(pieces[0].count(" ") + 1, s.ADAPTIVE_FIRST_CHUNK_WORDS)
        self.assertEqual(" ".join(pieces), text)


class TestProduceSpeakStream(unittest.TestCase):
    class _Stop:
        def is_set(self):
            return False

    def test_lookahead_emits_all_sentences(self):
        calls = []

        class FakeStreamer:
            def stream(self, text):
                calls.append(text)
                yield b"\x00\x00" * 10

        emitted = []
        s.produce_speak_stream_pcm(
            sentences_fn=lambda: iter(["One.", "Two.", "Three."]),
            streamer=FakeStreamer(),
            cap=200,
            stop=self._Stop(),
            emit=emitted.append,
            strip_md=lambda t: t,
            split_fn=lambda t, cap: [t],
        )
        self.assertEqual(len(emitted), 3)
        self.assertEqual(calls[0], "One.")
        self.assertEqual(calls, ["One.", "Two.", "Three."])

    def test_synthesis_error_does_not_kill_stream(self):
        state = {"n": 0}

        class FlakyStreamer:
            def stream(self, text):
                state["n"] += 1
                if state["n"] == 1:
                    raise RuntimeError("boom")
                yield b"\x00\x00" * 5

        emitted = []
        s.produce_speak_stream_pcm(
            sentences_fn=lambda: iter(["Bad.", "Good."]),
            streamer=FlakyStreamer(),
            cap=200,
            stop=self._Stop(),
            emit=emitted.append,
            strip_md=lambda t: t,
            split_fn=lambda t, cap: [t],
        )
        self.assertEqual(len(emitted), 1)  # only the good sentence


class TestFishStreamerStream(unittest.TestCase):
    def test_stream_converts_wav_to_pcm(self):
        pcm_in = b"".join((v).to_bytes(2, "little", signed=True) for v in [100, -100] * 8)
        wav = _wav_bytes(pcm_in, sample_rate=24000)

        fake_mod = mock.MagicMock()
        fake_mod.load_fish_key.return_value = "test-key"
        fake_mod.synthesize.return_value = wav

        with mock.patch.object(s, "_fish_tts_module", return_value=fake_mod):
            fs = s.FishStreamer({}, {"voice": "a" * 32})
            chunks = list(fs.stream("Hello."))

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], pcm_in)

    def test_stream_resamples_non_native_rate(self):
        pcm_in = b"".join((v).to_bytes(2, "little", signed=True) for v in [500] * 100)
        wav = _wav_bytes(pcm_in, sample_rate=44100)

        fake_mod = mock.MagicMock()
        fake_mod.load_fish_key.return_value = "test-key"
        fake_mod.synthesize.return_value = wav

        with mock.patch.object(s, "_fish_tts_module", return_value=fake_mod):
            fs = s.FishStreamer({}, {"voice": "a" * 32})
            chunks = list(fs.stream("Hello."))

        # 24/44.1 ratio: ~54 samples expected
        self.assertEqual(len(chunks[0]), 2 * round(100 * 24000 / 44100))

    def test_missing_key_raises(self):
        fake_mod = mock.MagicMock()
        fake_mod.load_fish_key.return_value = None
        with mock.patch.object(s, "_fish_tts_module", return_value=fake_mod):
            fs = s.FishStreamer.__new__(s.FishStreamer)
            s.StreamingTTSProvider.__init__(fs, {}, {})
            with self.assertRaises(RuntimeError):
                list(fs.stream("Hello."))


if __name__ == "__main__":
    unittest.main()
