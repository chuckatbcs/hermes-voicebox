#!/usr/bin/env python3
"""Unit tests for voicebox_tts bridge helpers."""
import unittest

from voicebox_tts import (
    _build_wav,
    _parse_wav_header,
    _split_sentences,
    get_base_url,
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


if __name__ == "__main__":
    unittest.main()
