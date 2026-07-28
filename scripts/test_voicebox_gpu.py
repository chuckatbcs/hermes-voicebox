#!/usr/bin/env python3
"""Unit tests for voicebox_gpu helpers."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import voicebox_gpu as gpu


class ConfigAndActivityTests(unittest.TestCase):
    def test_touch_and_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = gpu.touch_activity(home)
            self.assertTrue(path.is_file())
            age = gpu.activity_age_seconds(home)
            self.assertIsNotNone(age)
            self.assertLess(age, 5)

    def test_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg = gpu.save_config(
                {"stop_on_hermes_exit": False, "idle_unload_minutes": 7},
                home=home,
            )
            self.assertFalse(cfg["stop_on_hermes_exit"])
            self.assertEqual(cfg["idle_unload_minutes"], 7)
            again = gpu.load_config(home)
            self.assertFalse(again["stop_on_hermes_exit"])


class UnloadTests(unittest.TestCase):
    def test_unload_calls_named_and_default(self):
        calls = []

        def fake_http(method, url, body=None, timeout=30.0):
            calls.append((method, url, body))
            if url.endswith("/models/status"):
                return {
                    "models": [
                        {"model_name": "qwen-tts-0.6B", "loaded": True},
                        {"model_name": "kokoro", "loaded": False},
                    ]
                }
            return {"message": "ok"}

        with mock.patch.object(gpu, "_http_json", side_effect=fake_http):
            result = gpu.unload_models("http://127.0.0.1:17493")
        self.assertIn("qwen-tts-0.6B", result["unloaded"])
        urls = [c[1] for c in calls]
        self.assertTrue(any(u.endswith("/models/qwen-tts-0.6B/unload") for u in urls))
        self.assertTrue(any(u.endswith("/models/unload") for u in urls))

    def test_idle_check_skips_when_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            gpu.touch_activity(home)

            def fake_http(method, url, body=None, timeout=30.0):
                raise AssertionError("should not unload when fresh")

            with mock.patch.object(gpu, "_http_json", side_effect=fake_http):
                result = gpu.idle_check("http://127.0.0.1:17493", minutes=15, home=home)
            self.assertEqual(result["action"], "skip_fresh")


class CliSmokeTests(unittest.TestCase):
    def test_config_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = gpu.main(["--hermes-home", tmp, "config", "--idle-minutes", "9"])
            self.assertEqual(rc, 0)
            cfg = json.loads((Path(tmp) / "voicebox_gpu.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["idle_unload_minutes"], 9)


if __name__ == "__main__":
    unittest.main()
