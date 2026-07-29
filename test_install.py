#!/usr/bin/env python3
"""Tests for the cross-platform Hermes Voicebox installer."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import install
from installer import prereqs


class InstallerTests(unittest.TestCase):
    def test_build_snippet_quotes_spaces(self):
        bridge = Path("/home/some user/.hermes/scripts/voicebox_tts.py")
        snippet = install.build_snippet("python3", bridge)
        self.assertIn('command: python3 "/home/some user/.hermes/scripts/voicebox_tts.py"', snippet)
        self.assertIn(install.MARKER_BEGIN, snippet)
        self.assertIn(install.MARKER_END, snippet)
        self.assertIn(f"timeout: {install.COMMAND_TTS_TIMEOUT_SECONDS}", snippet)

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
            self.assertIn(f"timeout: {install.COMMAND_TTS_TIMEOUT_SECONDS}", text)

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

    def test_relocates_tts_block_parked_under_personalities(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text(
                "agent:\n"
                "  personalities:\n"
                f"{install.MARKER_BEGIN}\n"
                "tts:\n  provider: old\n"
                f"{install.MARKER_END}\n"
                "    helpful: You are helpful.\n"
                "terminal:\n  backend: local\n",
                encoding="utf-8",
            )
            snippet = install.build_snippet("python3", Path(tmp) / "scripts" / "voicebox_tts.py")
            result = install.merge_config(cfg, snippet)
            self.assertEqual(result, "relocated")
            text = cfg.read_text(encoding="utf-8")
            self.assertIn("provider: voicebox", text)
            self.assertNotIn("provider: old", text)
            # personalities entries must still be under personalities, not after a TTS hole
            self.assertIn("    helpful: You are helpful.", text)
            self.assertLess(text.index("personalities:"), text.index("helpful:"))
            self.assertLess(text.index("helpful:"), text.index(install.MARKER_BEGIN))


class ModelResolveTests(unittest.TestCase):
    def test_prefers_17b_qwen_and_skips_downloaded(self):
        status = {
            "models": [
                {"model_name": "qwen-tts-0.6B", "engine": "qwen", "downloaded": False, "size_mb": 1200},
                {"model_name": "qwen-tts-1.7B", "engine": "qwen", "downloaded": False, "size_mb": 3500},
                {"model_name": "kokoro", "engine": "kokoro", "downloaded": True, "size_mb": 350},
                {"model_name": "chatterbox-tts", "engine": "chatterbox", "downloaded": False, "size_mb": 3200},
                {"model_name": "whisper-base", "engine": "whisper", "downloaded": False, "size_mb": 300},
            ]
        }
        needed = prereqs.resolve_models_to_download(status, ("kokoro", "qwen", "chatterbox"))
        self.assertEqual(needed, ["qwen-tts-1.7B", "chatterbox-tts"])

    def test_all_profile_skips_whisper(self):
        status = {
            "models": [
                {"model_name": "kokoro", "engine": "kokoro", "downloaded": False},
                {"model_name": "whisper-small", "engine": "whisper", "downloaded": False},
            ]
        }
        needed = prereqs.resolve_models_to_download(status, None)
        self.assertEqual(needed, ["kokoro"])

    def test_does_not_invent_unknown_engine_ids(self):
        # Real Voicebox rejects POST /models/download {"model_name":"qwen"}
        status = {
            "models": [
                {"model_name": "kokoro", "engine": "kokoro", "downloaded": False},
                {"model_name": "qwen-tts-1.7B", "engine": "qwen", "downloaded": False},
                {"model_name": "chatterbox-tts", "engine": "chatterbox", "downloaded": False},
                {"model_name": "chatterbox-turbo", "engine": "chatterbox_turbo", "downloaded": False},
            ]
        }
        needed = prereqs.resolve_models_to_download(
            status, ("kokoro", "qwen", "chatterbox", "chatterbox_turbo")
        )
        self.assertEqual(
            needed,
            ["kokoro", "qwen-tts-1.7B", "chatterbox-tts", "chatterbox-turbo"],
        )
        self.assertNotIn("qwen", needed)
        self.assertNotIn("chatterbox", needed)

    def test_matches_name_when_engine_field_missing(self):
        status = {
            "models": [
                {"model_name": "qwen-tts-1.7B", "downloaded": False, "size_mb": 3500},
                {"model_name": "chatterbox-turbo", "downloaded": False, "size_mb": 1500},
            ]
        }
        needed = prereqs.resolve_models_to_download(status, ("qwen", "chatterbox_turbo"))
        self.assertEqual(needed, ["qwen-tts-1.7B", "chatterbox-turbo"])

    def test_dict_keyed_status_shape(self):
        status = {
            "kokoro": {"downloaded": False, "engine": "kokoro"},
            "qwen-tts-1.7B": {"downloaded": False, "engine": "qwen", "size_mb": 3500},
        }
        needed = prereqs.resolve_models_to_download(status, ("kokoro", "qwen"))
        self.assertEqual(needed, ["kokoro", "qwen-tts-1.7B"])


class SkipPrereqInstallTests(unittest.TestCase):
    def test_skip_prereqs_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            hermes = Path(tmp) / ".hermes"
            rc = install.main(["--hermes-dir", str(hermes), "--skip-prereqs", "--no-config"])
            self.assertEqual(rc, 0)
            plugin_dir = hermes / "desktop-plugins" / "voice-switcher"
            self.assertTrue((plugin_dir / "plugin.js").is_file())
            self.assertTrue((plugin_dir / "sample-voices.json").is_file())
            plugin_src = (plugin_dir / "plugin.js").read_text(encoding="utf-8")
            self.assertNotIn("from './sample-voices.js'", plugin_src)
            self.assertIn("SAMPLE_VOICES", plugin_src)
            self.assertTrue((hermes / "scripts" / "voicebox_tts.py").is_file())
            self.assertTrue((hermes / "scripts" / "voicebox_bind.py").is_file())


class ProfileHomeTests(unittest.TestCase):
    def test_resolve_default_and_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".hermes"
            root.mkdir()
            self.assertEqual(install.resolve_profile_hermes_dir("default", root), root.resolve())
            self.assertEqual(install.resolve_profile_hermes_dir("", root), root.resolve())
            named = install.resolve_profile_hermes_dir("work", root)
            self.assertEqual(named, (root / "profiles" / "work").resolve())

    def test_list_profiles_includes_root_and_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".hermes"
            (root / "profiles" / "alpha").mkdir(parents=True)
            (root / "profiles" / "beta").mkdir(parents=True)
            homes = install.list_profile_hermes_dirs(root)
            self.assertEqual(homes[0], root.resolve())
            self.assertIn((root / "profiles" / "alpha").resolve(), homes)
            self.assertIn((root / "profiles" / "beta").resolve(), homes)

    def test_all_profiles_merges_named_homes(self):
        src = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".hermes"
            work = root / "profiles" / "work"
            work.mkdir(parents=True)
            (work / "config.yaml").write_text("model:\n  default: test\n", encoding="utf-8")
            rc = install.main([
                "--hermes-dir", str(root),
                "--skip-prereqs",
                "--all-profiles",
            ])
            self.assertEqual(rc, 0)
            self.assertIn("provider: voicebox", (root / "config.yaml").read_text(encoding="utf-8"))
            self.assertIn("provider: voicebox", (work / "config.yaml").read_text(encoding="utf-8"))
            self.assertTrue((root / "scripts" / "voicebox_bind.py").is_file())
            self.assertTrue(
                (root / "desktop-plugins" / "voice-switcher" / "plugin.js").is_file()
            )
            self.assertTrue(
                (work / "desktop-plugins" / "voice-switcher" / "plugin.js").is_file()
            )
            self.assertTrue((work / "scripts" / "voicebox_tts.py").is_file())
            _ = src  # silence unused if flake8


class VoiceBindTests(unittest.TestCase):
    def test_bind_writes_binding_and_config_voice(self):
        import sys
        scripts = Path(__file__).resolve().parent / "scripts"
        sys.path.insert(0, str(scripts))
        import voicebox_bind  # noqa: E402

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg = home / "config.yaml"
            cfg.write_text(
                f"{install.MARKER_BEGIN}\n"
                "tts:\n"
                "  provider: voicebox\n"
                "  providers:\n"
                "    voicebox:\n"
                "      type: command\n"
                "      voice: default\n"
                "      output_format: wav\n"
                f"{install.MARKER_END}\n",
                encoding="utf-8",
            )
            info = voicebox_bind.bind_voice(
                "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                persona_key="jarvis",
                home=home,
            )
            self.assertEqual(info["config"], "updated")
            binding = voicebox_bind.read_binding(home)
            self.assertEqual(binding["voice_id"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            self.assertEqual(binding["persona_key"], "jarvis")
            text = cfg.read_text(encoding="utf-8")
            self.assertIn("voice: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", text)
            self.assertNotIn("voice: default", text)

    def test_bind_digit_leading_uuid_on_unmarked_config(self):
        """Regression: re.sub \\2 + '5d06…' must not become group reference 25."""
        import sys
        scripts = Path(__file__).resolve().parent / "scripts"
        sys.path.insert(0, str(scripts))
        import voicebox_bind  # noqa: E402

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg = home / "config.yaml"
            cfg.write_text(
                "tts:\n"
                "  provider: voicebox\n"
                "  providers:\n"
                "    voicebox:\n"
                "      type: command\n"
                "      voice: default\n"
                "      output_format: wav\n"
                "      timeout: 600\n",
                encoding="utf-8",
            )
            vid = "5d06502a-7a16-4d3a-91f7-54dec8e85179"
            info = voicebox_bind.bind_voice(vid, persona_key="jarvis", home=home)
            self.assertEqual(info["config"], "updated")
            text = cfg.read_text(encoding="utf-8")
            self.assertIn(f"voice: {vid}", text)
            self.assertNotIn("voice: default", text)


class TimeoutEnsureTests(unittest.TestCase):
    def test_inserts_timeout_under_providers_voicebox(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text(
                "tts:\n"
                "  provider: voicebox\n"
                "  providers:\n"
                "    voicebox:\n"
                "      type: command\n"
                "      voice: abc\n"
                "      output_format: wav\n"
                "stt:\n"
                "  enabled: true\n",
                encoding="utf-8",
            )
            self.assertEqual(install.ensure_voicebox_timeout(cfg), "inserted")
            text = cfg.read_text(encoding="utf-8")
            self.assertIn(f"timeout: {install.COMMAND_TTS_TIMEOUT_SECONDS}", text)
            self.assertEqual(install.ensure_voicebox_timeout(cfg), "unchanged")

    def test_updates_existing_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text(
                "tts:\n"
                "  providers:\n"
                "    voicebox:\n"
                "      type: command\n"
                "      timeout: 120\n",
                encoding="utf-8",
            )
            self.assertEqual(install.ensure_voicebox_timeout(cfg), "updated")
            self.assertIn(
                f"timeout: {install.COMMAND_TTS_TIMEOUT_SECONDS}",
                cfg.read_text(encoding="utf-8"),
            )

    def test_repairs_bogus_dash_personalities_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            samples = install.load_sample_voices(Path(__file__).resolve().parent)
            cfg.write_text(
                "agent:\n"
                "  personalities:\n"
                f"{install.PERSONA_MARKER_BEGIN}\n"
                "    cartman: |\n"
                "      hi\n"
                f"{install.PERSONA_MARKER_END}\n"
                "-personalities\n"
                "    cartman: 'dup'\n"
                "  max_turns: 1\n",
                encoding="utf-8",
            )
            result = install.merge_personalities_config(cfg, samples)
            self.assertEqual(result, "replaced")
            lines = cfg.read_text(encoding="utf-8").splitlines()
            self.assertNotIn("-personalities", lines)


class SpeakStreamHookTests(unittest.TestCase):
    def test_installs_streamer_and_import_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".hermes"
            tools = root / "hermes-agent" / "tools"
            tools.mkdir(parents=True)
            (tools / "tts_streaming.py").write_text("REGISTRY = {}\n", encoding="utf-8")
            cli = root / "hermes-agent" / "hermes_cli"
            cli.mkdir(parents=True)
            (cli / "web_server.py").write_text(
                "def speak_stream_ws():\n"
                "    def _produce():\n"
                "        try:\n"
                "            for sentence in _sentences():\n"
                "                cleaned = _strip_markdown_for_tts(sentence)\n"
                "                if not cleaned:\n"
                "                    continue\n"
                "                for piece in _split_text_for_speak_stream(cleaned, cap):\n"
                "                    for chunk in streamer.stream(piece):\n"
                "                        if stop.is_set():\n"
                "                            return\n"
                "                        loop.call_soon_threadsafe(chunks.put_nowait, chunk)\n"
                "        except Exception as exc:\n"
                '            _log.warning("speak-stream synthesis failed: %s", exc)\n'
                "        finally:\n"
                "            loop.call_soon_threadsafe(chunks.put_nowait, None)\n",
                encoding="utf-8",
            )
            result = install.install_speak_stream_hook(root)
            self.assertIn("appended_import", result)
            self.assertIn("produce_patched", result)
            self.assertTrue((tools / "voicebox_command_streamer.py").is_file())
            text = (tools / "tts_streaming.py").read_text(encoding="utf-8")
            self.assertIn(install.STREAMER_IMPORT_BEGIN, text)
            self.assertIn("voicebox_command_streamer", text)
            web = (cli / "web_server.py").read_text(encoding="utf-8")
            self.assertIn(install.PRODUCE_PATCH_BEGIN, web)
            self.assertIn("produce_speak_stream_pcm", web)
            self.assertNotIn("for chunk in streamer.stream(piece):", web)
            again = install.install_speak_stream_hook(root)
            self.assertIn("replaced_import", again)
            self.assertTrue(
                "produce_replaced" in again or "produce_unchanged" in again,
                again,
            )

    def test_produce_pattern_miss_leaves_web_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".hermes"
            tools = root / "hermes-agent" / "tools"
            tools.mkdir(parents=True)
            (tools / "tts_streaming.py").write_text("REGISTRY = {}\n", encoding="utf-8")
            cli = root / "hermes-agent" / "hermes_cli"
            cli.mkdir(parents=True)
            original = (
                "def speak_stream_ws():\n"
                "    def _produce():\n"
                "        pass\n"
            )
            web_path = cli / "web_server.py"
            web_path.write_text(original, encoding="utf-8")
            result = install.install_speak_stream_hook(root)
            self.assertIn("appended_import", result)
            self.assertIn("produce_pattern_miss", result)
            self.assertEqual(web_path.read_text(encoding="utf-8"), original)

    def test_install_files_requires_bind_and_streamer(self):
        src = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".hermes"
            plugin, bridge = install.install_files(src, root)
            self.assertTrue(plugin.is_file())
            self.assertTrue(bridge.is_file())
            self.assertTrue((root / "scripts" / "voicebox_bind.py").is_file())
            self.assertTrue((root / "scripts" / "voicebox_gpu.py").is_file())
            self.assertTrue((root / "scripts" / "diagnose_tts.sh").is_file())
            self.assertEqual(
                install.install_gpu_lifecycle(root, enable=True),
                "config_only_non_default_home",
            )
            self.assertTrue((root / "voicebox_gpu.json").is_file())

    def test_install_gpu_lifecycle_embeds_base_url_in_unit(self):
        src = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "home"
            fake_home.mkdir()
            root = fake_home / ".hermes"
            install.install_files(src, root)
            unit_dir = fake_home / ".config" / "systemd" / "user"
            custom = "http://127.0.0.1:18000"
            with mock.patch.object(install.Path, "home", return_value=fake_home), mock.patch(
                "subprocess.run", return_value=mock.Mock(returncode=0, stdout="", stderr="")
            ):
                status = install.install_gpu_lifecycle(
                    root, enable=True, base_url=custom
                )
            self.assertTrue(status.startswith("enabled:"), status)
            unit_text = (unit_dir / "voicebox-gpu-lifecycle.service").read_text(encoding="utf-8")
            self.assertIn(f"--base-url {custom}", unit_text)
            self.assertIn("lifecycle-daemon", unit_text)


class PrefetchPipelineTests(unittest.TestCase):
    def test_lookahead_starts_next_before_emit_finishes(self):
        """synth(N+1) must be submitted before emit(N) returns."""
        import importlib.util
        import threading
        import time

        # Load pipeline helpers without importing Hermes tools.tts_streaming.
        path = Path(__file__).resolve().parent / "scripts" / "hermes_voicebox_streamer.py"
        src = path.read_text(encoding="utf-8")
        # Stub the Hermes-only import so the module loads in unit tests.
        stubbed = src.replace(
            "from tools.tts_streaming import StreamingTTSProvider, register\n",
            "class StreamingTTSProvider:\n"
            "    sample_rate = 24000\n"
            "    channels = 1\n"
            "    def __init__(self, *a, **k): pass\n"
            "def register(name):\n"
            "    def wrap(cls): return cls\n"
            "    return wrap\n",
        )
        spec = importlib.util.spec_from_loader("vb_stream_test", loader=None)
        mod = importlib.util.module_from_spec(spec)
        exec(compile(stubbed, str(path), "exec"), mod.__dict__)

        order = []
        lock = threading.Lock()
        release_emit = threading.Event()

        class FakeStreamer(mod.StreamingTTSProvider):
            def stream(self, text):
                with lock:
                    order.append(f"synth-start:{text}")
                time.sleep(0.05)
                with lock:
                    order.append(f"synth-done:{text}")
                yield f"pcm:{text}".encode()

        def emit(pcm: bytes):
            with lock:
                order.append(f"emit:{pcm.decode()}")
            release_emit.wait(timeout=1)

        # Hold emit(a) until synth(b) has started.
        def run():
            mod.produce_speak_stream_pcm(
                sentences_fn=lambda: iter(["A.", "B."]),
                streamer=FakeStreamer({}, {}),
                cap=4000,
                stop=threading.Event(),
                emit=emit,
                strip_md=lambda s: s,
                split_fn=lambda s, _cap: [s.strip()],
            )

        t = threading.Thread(target=run)
        t.start()
        # Emit(A) blocks on release_emit; succeed only if B synth starts meanwhile.
        saw_overlap = False
        deadline = time.time() + 2
        while time.time() < deadline:
            with lock:
                if any(x.startswith("emit:pcm:A.") for x in order) and any(
                    x == "synth-start:B." for x in order
                ):
                    saw_overlap = True
                    break
            time.sleep(0.01)
        release_emit.set()
        t.join(timeout=2)
        self.assertTrue(saw_overlap, f"expected B synth during emit(A); order={order}")
        self.assertIn("emit:pcm:B.", order)


class PersonalitiesMergeTests(unittest.TestCase):
    def test_does_not_clobber_existing_agent_block(self):
        samples = install.load_sample_voices(Path(__file__).resolve().parent)
        self.assertGreaterEqual(len(samples), 5)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text(
                "agent:\n"
                "  model: gpt-test\n"
                "  max_tokens: 123\n",
                encoding="utf-8",
            )
            result = install.merge_personalities_config(cfg, samples)
            self.assertEqual(result, "inserted_under_agent")
            text = cfg.read_text(encoding="utf-8")
            self.assertIn("model: gpt-test", text)
            self.assertIn("max_tokens: 123", text)
            self.assertIn("personalities:", text)
            self.assertIn("vincent_price:", text)
            self.assertIn("glados:", text)
            self.assertEqual(len([ln for ln in text.splitlines() if ln.strip() == "agent:"]), 1)

    def test_repairs_personas_nested_under_tts_providers(self):
        samples = install.load_sample_voices(Path(__file__).resolve().parent)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text(
                "tts:\n"
                "  provider: voicebox\n"
                "  providers:\n"
                "    voicebox:\n"
                "      type: command\n"
                "      voice: default\n"
                "    vincent_price: 'bad nesting'\n"
                "    cartman: 'also bad'\n",
                encoding="utf-8",
            )
            result = install.merge_personalities_config(cfg, samples)
            self.assertEqual(result, "repaired_providers_nesting")
            data = __import__("yaml").safe_load(cfg.read_text(encoding="utf-8"))
            providers = data["tts"]["providers"]
            self.assertEqual(list(providers.keys()), ["voicebox"])
            personalities = data["agent"]["personalities"]
            self.assertIn("vincent_price", personalities)
            self.assertIn("cartman", personalities)
            self.assertIn("glados", personalities)

    def test_skips_when_markers_stripped_but_keys_exist(self):
        """Hermes often strips # BEGIN markers; re-install must not duplicate keys."""
        samples = install.load_sample_voices(Path(__file__).resolve().parent)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            # Simulate post-Hermes-rewrite: personalities present, markers gone.
            body = ["agent:", "  personalities:"]
            for s in samples:
                body.append(f"    {s['key']}: |")
                body.append(f"      {s['personality'][:40]}")
            cfg.write_text("\n".join(body) + "\n", encoding="utf-8")
            before = cfg.read_text(encoding="utf-8")
            result = install.merge_personalities_config(cfg, samples)
            self.assertEqual(result, "skipped_existing")
            after = cfg.read_text(encoding="utf-8")
            self.assertEqual(before, after)
            for s in samples:
                self.assertEqual(after.count(f"{s['key']}:"), 1)

    def test_rerun_with_markers_replaces_not_duplicates(self):
        samples = install.load_sample_voices(Path(__file__).resolve().parent)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text("agent:\n  model: keep-me\n", encoding="utf-8")
            r1 = install.merge_personalities_config(cfg, samples)
            self.assertEqual(r1, "inserted_under_agent")
            r2 = install.merge_personalities_config(cfg, samples)
            self.assertEqual(r2, "replaced")
            text = cfg.read_text(encoding="utf-8")
            self.assertEqual(text.count("vincent_price:"), 1)
            self.assertEqual(text.count("jarvis:"), 1)
            self.assertIn("keep-me", text)

    def test_appended_agent_snippet_after_tts_replaces_cleanly(self):
        """Markers after a TTS providers block must replace, not false-repair."""
        samples = install.load_sample_voices(Path(__file__).resolve().parent)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text(
                "# BEGIN hermes-voicebox\n"
                "tts:\n"
                "  provider: voicebox\n"
                "  providers:\n"
                "    voicebox:\n"
                "      type: command\n"
                "      voice: default\n"
                "      timeout: 600\n"
                "# END hermes-voicebox\n",
                encoding="utf-8",
            )
            r1 = install.merge_personalities_config(cfg, samples)
            self.assertEqual(r1, "appended")
            r2 = install.merge_personalities_config(cfg, samples)
            self.assertEqual(r2, "replaced")
            text = cfg.read_text(encoding="utf-8")
            data = __import__("yaml").safe_load(text)
            self.assertEqual(text.count("vincent_price:"), 1)
            self.assertEqual(text.count("jarvis:"), 1)
            self.assertIn("voicebox", data["tts"]["providers"])
            self.assertIn("jarvis", data["agent"]["personalities"])


if __name__ == "__main__":
    unittest.main()
