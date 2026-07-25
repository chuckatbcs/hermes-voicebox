#!/usr/bin/env python3
"""Tests for the cross-platform Hermes Voicebox installer."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import install


class InstallerTests(unittest.TestCase):
    def test_build_snippet_quotes_spaces(self):
        bridge = Path("/home/some user/.hermes/scripts/voicebox_tts.py")
        snippet = install.build_snippet("python3", bridge)
        self.assertIn('command: python3 "/home/some user/.hermes/scripts/voicebox_tts.py"', snippet)
        self.assertIn(install.MARKER_BEGIN, snippet)
        self.assertIn(install.MARKER_END, snippet)

    def test_install_and_create_config(self):
        src = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            hermes = Path(tmp) / ".hermes"
            plugin_dst, bridge_dst = install.install_files(src, hermes)
            self.assertTrue(plugin_dst.is_file())
            self.assertTrue(bridge_dst.is_file())

            snippet = install.build_snippet("python3", bridge_dst)
            result = install.merge_config(hermes / "config.yaml", snippet)
            self.assertEqual(result, "created")
            text = (hermes / "config.yaml").read_text(encoding="utf-8")
            self.assertIn("provider: voicebox", text)
            self.assertIn("type: command", text)

    def test_replace_marked_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text(
                "other: true\n\n"
                f"{install.MARKER_BEGIN}\n"
                "tts:\n  provider: old\n"
                f"{install.MARKER_END}\n",
                encoding="utf-8",
            )
            snippet = install.build_snippet("python3", Path(tmp) / "scripts" / "voicebox_tts.py")
            result = install.merge_config(cfg, snippet)
            self.assertEqual(result, "replaced")
            text = cfg.read_text(encoding="utf-8")
            self.assertIn("provider: voicebox", text)
            self.assertNotIn("provider: old", text)
            self.assertTrue(cfg.with_suffix(".yaml.bak").exists())

    def test_skip_existing_unmarked_tts(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text("tts:\n  provider: something_else\n", encoding="utf-8")
            snippet = install.build_snippet("python3", Path(tmp) / "voicebox_tts.py")
            result = install.merge_config(cfg, snippet)
            self.assertEqual(result, "skipped_manual")
            self.assertEqual(cfg.read_text(encoding="utf-8"), "tts:\n  provider: something_else\n")


if __name__ == "__main__":
    unittest.main()
