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
                # Return health as "alive" so idle_check proceeds past the
                # reachable check; then raise if unload is attempted.
                if url.endswith("/health"):
                    return {"ok": True, "model_loaded": True}
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


class HermesReturnRestartTests(unittest.TestCase):
    def test_daemon_starts_voicebox_when_hermes_returns(self):
        """After stop-on-exit, coming back from down→up must call start_voicebox()."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            gpu.save_config(
                {
                    "stop_on_hermes_exit": True,
                    "idle_unload_enabled": False,
                    "idle_unload_minutes": 15,
                },
                home=home,
            )
            daemon = gpu.LifecycleDaemon(base_url="http://127.0.0.1:17493", home=home, control_port=0)
            daemon._hermes_was_up = False  # simulate prior Hermes-exit stop
            daemon._stop = mock.Mock()
            # First wait: 0s (loop check), second wait: 5s (verify startup), third: True (exit)
            daemon._stop.wait = mock.Mock(side_effect=[False, False, True])

            # get_health returns None first (Voicebox down → triggers start_voicebox),
            # then returns a dict (Voicebox came up after start).
            health_mock = mock.Mock(side_effect=[None, None, {"ok": True, "model_loaded": False}])

            with mock.patch.object(gpu, "hermes_desktop_running", return_value=True), \
                 mock.patch.object(gpu, "get_health", side_effect=health_mock), \
                 mock.patch.object(gpu, "start_voicebox", return_value={"actions": []}) as start, \
                 mock.patch.object(gpu, "idle_check", side_effect=lambda *a, **kw: {"action": "ok"}):
                daemon._loop()

            start.assert_called_once()
            self.assertTrue(daemon._hermes_was_up)


if __name__ == "__main__":
    unittest.main()
