#!/usr/bin/env python3
"""Unit tests for voicebox_tts bridge helpers."""
import unittest

from voicebox_tts import (
    _build_wav,
    _parse_wav_header,
    _profile_tts_preflight,
    _split_one_sentence_chunks,
    _split_sentences,
    get_base_url,
    resolve_profile_id,
    split_tts_chunks,
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


class TestSentenceIsolatedChunks(unittest.TestCase):
    def test_one_sentence_per_chunk_for_clones(self):
        text = (
            "Ugh, what do you want? I was in the middle of something important. "
            "Make it quick, k? I don't have all day."
        )
        chunks = split_tts_chunks(text, "chatterbox_turbo")
        self.assertEqual(len(chunks), 4)
        self.assertTrue(all("?" in c or "." in c for c in chunks))

    def test_kokoro_still_packs_sentences(self):
        text = "Hello world. " * 20
        clone_chunks = split_tts_chunks(text, "chatterbox_turbo")
        kokoro_chunks = split_tts_chunks(text, "kokoro")
        self.assertGreater(len(clone_chunks), len(kokoro_chunks))

    def test_paragraph_break_without_punct(self):
        text = "First paragraph line\n\nSecond paragraph line"
        chunks = _split_one_sentence_chunks(text, max_chars=400)
        self.assertEqual(chunks, ["First paragraph line", "Second paragraph line"])


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
            raise AssertionError("should not fetch when CLI voice is a valid UUID")

        # UUID profile ID wins without any network calls
        valid_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        self.assertEqual(
            resolve_profile_id(valid_uuid, "http://127.0.0.1:17493", get_json=get_json),
            valid_uuid,
        )
        self.assertEqual(calls, [])

    def test_cli_voice_name_resolves_via_profiles(self):
        calls = []

        def get_json(url):
            calls.append(url)
            if url.endswith("/profiles"):
                return [
                    {"id": "uuid-jarvis", "name": "Jarvis"},
                    {"id": "uuid-cartman", "name": "Eric Cartman"},
                ]
            raise AssertionError(url)

        # Name resolves to matching profile's UUID
        self.assertEqual(
            resolve_profile_id("Jarvis", "http://127.0.0.1:17493", get_json=get_json),
            "uuid-jarvis",
        )
        self.assertEqual(calls, ["http://127.0.0.1:17493/profiles"])

    def test_binding_json_beats_active_voice(self):
        import os
        import tempfile
        from pathlib import Path

        def get_json(url):
            if url.endswith("/settings/active-voice"):
                return {"voice_id": "global-should-not-win"}
            if url.endswith("/profiles"):
                return [{"id": "first-profile"}]
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "voicebox_binding.json").write_text(
                '{"voice_id": "bound-profile-id"}\n', encoding="utf-8"
            )
            old_home = os.environ.get("HERMES_HOME")
            old_dir = os.environ.get("HERMES_DIR")
            os.environ["HERMES_HOME"] = tmp
            os.environ.pop("HERMES_DIR", None)
            try:
                self.assertEqual(
                    resolve_profile_id("default", "http://127.0.0.1:17493", get_json=get_json),
                    "bound-profile-id",
                )
            finally:
                if old_home is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = old_home
                if old_dir is None:
                    os.environ.pop("HERMES_DIR", None)
                else:
                    os.environ["HERMES_DIR"] = old_dir

    def test_hermes_home_wins_over_hermes_dir(self):
        import os
        import tempfile
        from pathlib import Path

        def get_json(url):
            raise AssertionError(f"unexpected fetch {url}")

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            other = Path(tmp) / "other"
            home.mkdir()
            other.mkdir()
            (home / "voicebox_binding.json").write_text(
                '{"voice_id": "from-hermes-home"}\n', encoding="utf-8"
            )
            (other / "voicebox_binding.json").write_text(
                '{"voice_id": "from-hermes-dir"}\n', encoding="utf-8"
            )
            old_home = os.environ.get("HERMES_HOME")
            old_dir = os.environ.get("HERMES_DIR")
            os.environ["HERMES_HOME"] = str(home)
            os.environ["HERMES_DIR"] = str(other)
            try:
                self.assertEqual(
                    resolve_profile_id("default", "http://127.0.0.1:17493", get_json=get_json),
                    "from-hermes-home",
                )
            finally:
                if old_home is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = old_home
                if old_dir is None:
                    os.environ.pop("HERMES_DIR", None)
                else:
                    os.environ["HERMES_DIR"] = old_dir

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
            old_home = os.environ.get("HERMES_HOME")
            old_dir = os.environ.get("HERMES_DIR")
            os.environ["HERMES_HOME"] = tmp
            os.environ["HERMES_DIR"] = tmp
            # Ensure no sidecar leaks from the developer machine.
            Path(tmp, "voicebox_active_voice.json").unlink(missing_ok=True)
            Path(tmp, "voicebox_binding.json").unlink(missing_ok=True)
            try:
                self.assertEqual(
                    resolve_profile_id("default", "http://127.0.0.1:17493", get_json=get_json),
                    "first-profile",
                )
            finally:
                if old_home is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = old_home
                if old_dir is None:
                    os.environ.pop("HERMES_DIR", None)
                else:
                    os.environ["HERMES_DIR"] = old_dir


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


class TestEngineNormalize(unittest.TestCase):
    def test_qwen_fast_alias(self):
        from voicebox_tts import normalize_engine, model_size_for_engine

        self.assertEqual(normalize_engine("qwen_fast"), "qwen")
        self.assertEqual(normalize_engine("Qwen_Fast"), "qwen")
        self.assertEqual(normalize_engine("chatterbox_turbo"), "chatterbox_turbo")
        self.assertIsNone(normalize_engine(None))
        self.assertEqual(model_size_for_engine("qwen_fast"), "0.6B")
        self.assertEqual(model_size_for_engine("qwen"), "0.6B")
        self.assertIsNone(model_size_for_engine("chatterbox_turbo"))


class TestSanitizeSpokenText(unittest.TestCase):
    def test_strips_bracket_stage_directions_for_qwen(self):
        from voicebox_tts import sanitize_spoken_text_for_voicebox

        out = sanitize_spoken_text_for_voicebox(
            "What do you want? [whiny voice] Respect my authoritah!",
            "qwen",
        )
        self.assertNotIn("[", out)
        self.assertNotIn("whiny", out.lower())
        self.assertIn("Respect my authoritah", out)

    def test_keeps_chatterbox_paralinguistic_tags(self):
        from voicebox_tts import sanitize_spoken_text_for_voicebox

        out = sanitize_spoken_text_for_voicebox(
            "Hi there [chuckle], got a minute?",
            "chatterbox_turbo",
        )
        self.assertIn("[chuckle]", out)

    def test_strips_unknown_brackets_even_on_chatterbox(self):
        from voicebox_tts import sanitize_spoken_text_for_voicebox

        out = sanitize_spoken_text_for_voicebox(
            "Hello [sarcastically] friend [laugh]",
            "chatterbox_turbo",
        )
        self.assertNotIn("sarcastically", out)
        self.assertIn("[laugh]", out)

    def test_strips_markdown_emphasis(self):
        from voicebox_tts import sanitize_spoken_text_for_voicebox

        out = sanitize_spoken_text_for_voicebox(
            "Something *extremely* important",
            "qwen",
        )
        self.assertEqual(out, "Something extremely important")


class TestCudaOomHelpers(unittest.TestCase):
    def test_detects_cuda_oom(self):
        from voicebox_tts import _is_cuda_oom_error

        self.assertTrue(_is_cuda_oom_error("torch.cuda.OutOfMemoryError: CUDA out of memory"))
        self.assertTrue(_is_cuda_oom_error("RuntimeError: CUDA OOM while allocating"))
        self.assertFalse(_is_cuda_oom_error("HTTP 500: unspecified error"))
        self.assertFalse(_is_cuda_oom_error("room temperature high"))

    def test_unload_result_shape(self):
        from voicebox_tts import unload_voicebox_models

        calls = []

        def fake_get(url):
            calls.append(("GET", url))
            return {
                "models": [
                    {"model_name": "qwen", "loaded": True, "downloaded": True},
                    {"model_name": "kokoro", "loaded": False},
                ]
            }

        # Patch module helpers used by unload
        import voicebox_tts as vb

        old_get = vb._get_json
        old_post = vb._post_json_ok
        old_vram = vb._nvidia_vram_mb
        vb._get_json = fake_get
        vb._post_json_ok = lambda url, body=None, timeout=60.0: (True, "ok")
        vb._nvidia_vram_mb = lambda: (2048, 4096)
        try:
            result = unload_voicebox_models("http://127.0.0.1:17493")
        finally:
            vb._get_json = old_get
            vb._post_json_ok = old_post
            vb._nvidia_vram_mb = old_vram
        self.assertIn("qwen", result["unloaded"])
        self.assertTrue(result["default"]["ok"])
        self.assertEqual(result["vram_before"]["used_mb"], 2048)


if __name__ == "__main__":
    unittest.main()
