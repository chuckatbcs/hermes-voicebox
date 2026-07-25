#!/usr/bin/env python3
"""Unit tests for voicebox_tts bridge helpers."""
import unittest

from voicebox_tts import (
    _build_wav,
    _parse_wav_header,
    _profile_tts_preflight,
    _split_sentences,
    get_base_url,
    resolve_profile_id,
)


def _pcm_wav(pcm: bytes, sample_rate=24000, channels=1, bits=16) -> bytes:
    return _build_wav([pcm], sample_rate, channels, bits)


class TestSplitSentences(unittest.TestCase):
    def test_respects_max_chars_on_sentence_boundaries(self):
        text = "Hello world. " * 40
        chunks = _split_sentences(text, max_chars=80)
        self.assertTrue(chunks)
        self.assertTrue(all(len(c) <= 80 for c in chunks))

    def test_hard_splits_oversized_sentence(self):
        words = " ".join(f"word{i}" for i in range(100))
        chunks = _split_sentences(words, max_chars=40)
        self.assertTrue(all(len(c) <= 40 for c in chunks))
        self.assertEqual(" ".join(chunks).replace("  ", " "), words)


class TestWavHelpers(unittest.TestCase):
    def test_roundtrip_header(self):
        pcm = b"\x00\x01" * 100
        wav = _pcm_wav(pcm, sample_rate=16000, channels=1, bits=16)
        sr, nc, bps, offset, data_len = _parse_wav_header(wav)
        self.assertEqual((sr, nc, bps), (16000, 1, 16))
        self.assertEqual(wav[offset:offset + data_len], pcm)

    def test_rejects_non_wav(self):
        with self.assertRaises(ValueError):
            _parse_wav_header(b"not-a-wav-file")


class TestGetBaseUrl(unittest.TestCase):
    def test_cli_flag_wins(self):
        self.assertEqual(
            get_base_url("http://127.0.0.1:9999/"),
            "http://127.0.0.1:9999",
        )

    def test_env_port(self):
        import os
        old = os.environ.get("VOICEBOX_PORT")
        os.environ["VOICEBOX_PORT"] = "12345"
        try:
            self.assertEqual(get_base_url(None), "http://127.0.0.1:12345")
        finally:
            if old is None:
                del os.environ["VOICEBOX_PORT"]
            else:
                os.environ["VOICEBOX_PORT"] = old


class TestResolveProfileId(unittest.TestCase):
    def test_cli_voice_wins(self):
        calls = []

        def get_json(url):
            calls.append(url)
            raise AssertionError("should not fetch when CLI voice is set")

        self.assertEqual(
            resolve_profile_id("profile-123", "http://127.0.0.1:17493", get_json=get_json),
            "profile-123",
        )
        self.assertEqual(calls, [])

    def test_falls_back_when_active_voice_missing(self):
        import os
        import tempfile
        from pathlib import Path

        class MissingActiveVoice(Exception):
            pass

        def get_json(url):
            if url.endswith("/settings/active-voice"):
                raise MissingActiveVoice("Not Found")
            if url.endswith("/profiles"):
                return [{"id": "first-profile"}]
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("HERMES_DIR")
            os.environ["HERMES_DIR"] = tmp
            # Ensure no sidecar leaks from the developer machine.
            Path(tmp, "voicebox_active_voice.json").unlink(missing_ok=True)
            try:
                self.assertEqual(
                    resolve_profile_id("default", "http://127.0.0.1:17493", get_json=get_json),
                    "first-profile",
                )
            finally:
                if old is None:
                    del os.environ["HERMES_DIR"]
                else:
                    os.environ["HERMES_DIR"] = old


class TestProfilePreflight(unittest.TestCase):
    def test_preset_missing_voice_id(self):
        err = _profile_tts_preflight({
            "name": "Jarvis",
            "voice_type": "preset",
            "preset_voice_id": None,
        })
        self.assertIsNotNone(err)
        self.assertIn("preset_voice_id", err)

    def test_cloned_without_samples(self):
        err = _profile_tts_preflight({
            "name": "Mine",
            "voice_type": "cloned",
            "sample_count": 0,
        })
        self.assertIsNotNone(err)
        self.assertIn("reference samples", err)

    def test_ok_preset(self):
        self.assertIsNone(_profile_tts_preflight({
            "name": "Jarvis",
            "voice_type": "preset",
            "preset_voice_id": "bm_george",
            "default_engine": "kokoro",
        }))


if __name__ == "__main__":
    unittest.main()
