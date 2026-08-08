#!/usr/bin/env python3
"""Tests for the cross-platform Hermes Voicebox installer."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

import install
from installer import prereqs


class InstallerTests(unittest.TestCase):
    def test_build_snippet_quotes_spaces(self):
        """Paths containing spaces must be quoted, using native separators."""
        bridge = Path("/home/some user/.hermes/scripts/voicebox_tts.py")
        snippet = install.build_snippet("python3", bridge)
        # str(Path) yields native separators (\\ on Windows, / on POSIX); the
        # contract under test is the quoting, not the separator flavour.
        self.assertIn(f'command: python3 "{bridge}"', snippet)
        self.assertIn(install.MARKER_BEGIN, snippet)
        self.assertIn(install.MARKER_END, snippet)
        self.assertIn(f"timeout: {install.COMMAND_TTS_TIMEOUT_SECONDS}", snippet)

    def test_build_snippet_leaves_spaceless_path_unquoted(self):
        bridge = Path("/home/user/.hermes/scripts/voicebox_tts.py")
        snippet = install.build_snippet("python3", bridge)
        self.assertIn(f"command: python3 {bridge} ", snippet)
        self.assertNotIn(f'"{bridge}"', snippet)

    def test_build_snippet_quotes_windows_style_path(self):
        """A Windows path with spaces is quoted the same way as a POSIX one."""
        bridge = Path(r"C:\Program Files\Hermes\scripts\voicebox_tts.py")
        snippet = install.build_snippet("py -3", bridge)
        self.assertIn(f'command: py -3 "{bridge}"', snippet)

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

    def test_normalize_tts_commands_fixes_tilde_and_python3(self):
        """A literal ~/.hermes path and python3 launcher are rewritten to the
        absolute path + correct launcher, preserving all trailing args."""
        bridge = Path(r"C:\Users\cblac\.hermes\scripts\voicebox_tts.py")
        text = (
            "tts:\n"
            "  providers:\n"
            "    fish:\n"
            "      command: python3 ~/.hermes/scripts/voicebox_tts.py --provider fish "
            "--text-file {input_path} --out {output_path} --fish-label jarvis\n"
        )
        fixed, rewritten = install.normalize_tts_commands(text, bridge, "py -3")
        self.assertEqual(len(rewritten), 1)
        self.assertIn(
            "command: py -3 C:\\Users\\cblac\\.hermes\\scripts\\voicebox_tts.py "
            "--provider fish --text-file {input_path} --out {output_path} --fish-label jarvis",
            fixed,
        )
        self.assertNotIn("~/.hermes", fixed)
        self.assertNotIn("python3", fixed.split("command:")[1].split()[0])

    def test_normalize_tts_commands_leaves_good_command_alone(self):
        """A correct absolute-path command (any platform) is untouched."""
        bridge = Path(r"C:\Users\cblac\.hermes\scripts\voicebox_tts.py")
        text = (
            "tts:\n"
            "  providers:\n"
            "    voicebox:\n"
            "      command: py -3 C:\\Users\\cblac\\.hermes\\scripts\\voicebox_tts.py "
            "--text-file {input_path} --out {output_path} --voice 3f05\n"
        )
        fixed, rewritten = install.normalize_tts_commands(text, bridge, "py -3")
        self.assertEqual(rewritten, [])
        self.assertEqual(fixed, text)

    def test_normalize_tts_commands_leaves_good_posix_command_alone(self):
        """A correct POSIX (Linux) command must be a NO-OP even when the test
        host is Windows. Uses PurePosixPath so the bridge path keeps forward
        slashes; a correct `python3 /abs/path` command must not be rewritten.
        This guards the 'Linux version stays clean' contract."""
        bridge = PurePosixPath("/home/cblac/.hermes/scripts/voicebox_tts.py")
        text = (
            "tts:\n"
            "  providers:\n"
            "    voicebox:\n"
            "      command: python3 /home/cblac/.hermes/scripts/voicebox_tts.py "
            "--text-file {input_path} --out {output_path} --voice 3f05\n"
        )
        fixed, rewritten = install.normalize_tts_commands(text, bridge, "python3")
        self.assertEqual(rewritten, [])
        self.assertEqual(fixed, text)

    def test_merge_config_repairs_stale_unmarked_block(self):
        """A pre-existing, unmarked config with a broken Windows command is
        repaired in place (returns 'repaired') rather than skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text(
                "tts:\n"
                "  provider: fish\n"
                "  providers:\n"
                "    fish:\n"
                "      type: command\n"
                "      command: python3 ~/.hermes/scripts/voicebox_tts.py --provider fish "
                "--text-file {input_path} --out {output_path} --fish-label jarvis\n",
                encoding="utf-8",
            )
            bridge = Path(r"C:\Users\cblac\.hermes\scripts\voicebox_tts.py")
            snippet = install.build_snippet("py -3", bridge)
            result = install.merge_config(
                cfg, snippet, bridge_path=bridge, python_cmd="py -3"
            )
            self.assertEqual(result, "repaired")
            text = cfg.read_text(encoding="utf-8")
            self.assertIn(
                "command: py -3 C:\\Users\\cblac\\.hermes\\scripts\\voicebox_tts.py "
                "--provider fish --text-file {input_path} --out {output_path} --fish-label jarvis",
                text,
            )
            self.assertNotIn("~/.hermes", text)


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
            # A throwaway --hermes-dir must never get a real OS service unit,
            # on any platform with a supported supervisor.
            self.assertEqual(
                install.install_gpu_lifecycle(root, enable=True),
                "config_only_non_default_home",
            )
            self.assertTrue((root / "voicebox_gpu.json").is_file())

    def test_service_backend_maps_each_platform(self):
        self.assertEqual(install.service_backend("Linux"), "systemd")
        self.assertEqual(install.service_backend("Windows"), "schtasks")
        self.assertIsNone(install.service_backend("Darwin"))

    def test_install_gpu_lifecycle_embeds_base_url_in_unit(self):
        """systemd backend: ExecStart carries interpreter, home and base URL."""
        src = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "home"
            fake_home.mkdir()
            root = fake_home / ".hermes"
            install.install_files(src, root)
            unit_dir = fake_home / ".config" / "systemd" / "user"
            custom = "http://127.0.0.1:18000"
            with mock.patch.object(install, "platform") as plat, mock.patch.object(
                install.Path, "home", return_value=fake_home
            ), mock.patch(
                "subprocess.run", return_value=mock.Mock(returncode=0, stdout="", stderr="")
            ):
                plat.system.return_value = "Linux"
                status = install.install_gpu_lifecycle(
                    root, enable=True, base_url=custom
                )
            self.assertTrue(status.startswith("enabled:"), status)
            unit_text = (unit_dir / "voicebox-gpu-lifecycle.service").read_text(encoding="utf-8")
            self.assertIn(f"--base-url {custom}", unit_text)
            self.assertIn("lifecycle-daemon", unit_text)
            self.assertIn(str(root), unit_text)

    def test_install_gpu_lifecycle_embeds_base_url_in_scheduled_task(self):
        """schtasks backend: the task XML mirrors the systemd ExecStart contract."""
        src = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "home"
            fake_home.mkdir()
            root = fake_home / ".hermes"
            install.install_files(src, root)
            custom = "http://127.0.0.1:18000"
            calls: list[list[str]] = []

            def _record(args, *a, **kw):
                calls.append(list(args))
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch.object(install, "platform") as plat, mock.patch.object(
                install.Path, "home", return_value=fake_home
            ), mock.patch("subprocess.run", side_effect=_record):
                plat.system.return_value = "Windows"
                status = install.install_gpu_lifecycle(
                    root, enable=True, base_url=custom
                )

            self.assertTrue(status.startswith("enabled:"), status)
            task_xml = root / "installer" / "voicebox-gpu-lifecycle.xml"
            self.assertTrue(task_xml.is_file())
            text = task_xml.read_text(encoding="utf-16")
            self.assertIn(f"--base-url {custom}", text)
            self.assertIn("lifecycle-daemon", text)
            self.assertIn(str(root), text)
            # The task must actually be registered with the OS supervisor.
            schtasks_calls = [c for c in calls if c and c[0] == "schtasks"]
            self.assertTrue(
                any("/Create" in c for c in schtasks_calls), schtasks_calls
            )

    def test_windows_task_template_is_well_formed_xml(self):
        """
        Guards a real failure mode: schtasks rejects malformed XML ("incorrect
        comment syntax") while a mocked subprocess.run happily returns 0. Parse
        the template on every platform so a bad edit fails CI on Linux too.
        """
        import xml.etree.ElementTree as ET

        template = (
            Path(__file__).resolve().parent
            / "installer"
            / "windows"
            / "voicebox-gpu-lifecycle.xml"
        )
        self.assertTrue(template.is_file(), template)
        raw = template.read_text(encoding="utf-8")
        # Must parse; '--' inside an XML comment is illegal and raises here.
        ET.fromstring(raw)
        self.assertIn("__VOICEBOX_GPU_COMMAND__", raw)
        self.assertIn("__VOICEBOX_GPU_ARGUMENTS__", raw)

    def test_rendered_windows_task_is_well_formed_xml(self):
        """The substituted task (real paths, real base URL) must still parse."""
        import xml.etree.ElementTree as ET

        src = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "home"
            fake_home.mkdir()
            root = fake_home / ".hermes"
            install.install_files(src, root)
            with mock.patch.object(install, "platform") as plat, mock.patch.object(
                install.Path, "home", return_value=fake_home
            ), mock.patch(
                "subprocess.run", return_value=mock.Mock(returncode=0, stdout="", stderr="")
            ):
                plat.system.return_value = "Windows"
                install.install_gpu_lifecycle(
                    root, enable=True, base_url="http://127.0.0.1:18000"
                )
            rendered = (root / "installer" / "voicebox-gpu-lifecycle.xml").read_text(
                encoding="utf-16"
            )
            tree = ET.fromstring(rendered)
            ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
            args = tree.find(".//t:Exec/t:Arguments", ns)
            self.assertIsNotNone(args)
            self.assertIn("lifecycle-daemon", args.text or "")
            self.assertNotIn("__VOICEBOX_GPU_", rendered)

    def test_systemd_template_has_no_unresolved_placeholders(self):
        template = (
            Path(__file__).resolve().parent
            / "installer"
            / "systemd"
            / "voicebox-gpu-lifecycle.service"
        )
        self.assertTrue(template.is_file(), template)
        text = template.read_text(encoding="utf-8")
        self.assertIn("ExecStart=", text)
        self.assertIn("lifecycle-daemon", text)

    def test_systemd_failure_is_not_reported_as_enabled(self):
        """
        Regression: the systemd branch ignored returncode and claimed
        "enabled:" even when systemctl failed (e.g. WSL / containers with no
        user bus). Mirrors the Windows backend's honest reporting.
        """
        src = Path(__file__).resolve().parent
        cases = [
            ("Failed to connect to bus: No such file or directory", "needs_user_bus"),
            ("Job for voicebox-gpu-lifecycle.service failed", "unit_written"),
        ]
        for stderr, expected in cases:
            with self.subTest(stderr=stderr):
                with tempfile.TemporaryDirectory() as tmp:
                    fake_home = Path(tmp) / "home"
                    fake_home.mkdir()
                    root = fake_home / ".hermes"
                    install.install_files(src, root)
                    with mock.patch.object(install, "platform") as plat, \
                         mock.patch.object(install.Path, "home", return_value=fake_home), \
                         mock.patch(
                             "subprocess.run",
                             return_value=mock.Mock(returncode=1, stdout="", stderr=stderr),
                         ):
                        plat.system.return_value = "Linux"
                        status = install.install_gpu_lifecycle(root, enable=True)
                    self.assertTrue(
                        status.startswith(expected), f"{status!r} should start with {expected!r}"
                    )
                    self.assertFalse(status.startswith("enabled:"), status)
                    # The unit must still be on disk for manual activation.
                    unit = fake_home / ".config" / "systemd" / "user" / install.UNIT_NAME
                    self.assertTrue(unit.is_file(), unit)

    def test_systemd_success_reports_enabled(self):
        src = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "home"
            fake_home.mkdir()
            root = fake_home / ".hermes"
            install.install_files(src, root)
            with mock.patch.object(install, "platform") as plat, \
                 mock.patch.object(install.Path, "home", return_value=fake_home), \
                 mock.patch(
                     "subprocess.run",
                     return_value=mock.Mock(returncode=0, stdout="", stderr=""),
                 ):
                plat.system.return_value = "Linux"
                status = install.install_gpu_lifecycle(root, enable=True)
            self.assertTrue(status.startswith("enabled:"), status)

    def test_both_backends_report_failure_consistently(self):
        """Neither backend may claim success when its supervisor refused."""
        src = Path(__file__).resolve().parent
        for system, stderr in (
            ("Linux", "Failed to connect to bus: No such file or directory"),
            ("Windows", "ERROR: Access is denied."),
        ):
            with self.subTest(system=system):
                with tempfile.TemporaryDirectory() as tmp:
                    fake_home = Path(tmp) / "home"
                    fake_home.mkdir()
                    root = fake_home / ".hermes"
                    install.install_files(src, root)
                    with mock.patch.object(install, "platform") as plat, \
                         mock.patch.object(install.Path, "home", return_value=fake_home), \
                         mock.patch(
                             "subprocess.run",
                             return_value=mock.Mock(returncode=1, stdout="", stderr=stderr),
                         ):
                        plat.system.return_value = system
                        status = install.install_gpu_lifecycle(root, enable=True)
                    self.assertFalse(status.startswith("enabled:"), f"{system}: {status}")

    def test_install_gpu_lifecycle_config_only_on_unsupported_platform(self):
        src = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "home"
            fake_home.mkdir()
            root = fake_home / ".hermes"
            install.install_files(src, root)
            with mock.patch.object(install, "platform") as plat, mock.patch.object(
                install.Path, "home", return_value=fake_home
            ):
                plat.system.return_value = "Darwin"
                status = install.install_gpu_lifecycle(root, enable=True)
            self.assertTrue(
                status.startswith("config_only_unsupported_platform"), status
            )


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
    """Demo voices are clones now (no Kokoro persona presets), so
    load_sample_voices() returns [] and merge_personalities_config() skips
    seeding — but still repairs a bogus bare '-personalities' line."""

    def test_load_sample_voices_empty(self):
        samples = install.load_sample_voices(Path(__file__).resolve().parent)
        self.assertEqual(samples, [])

    def test_skips_when_no_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text(
                "agent:\n"
                "  model: gpt-test\n"
                "  max_tokens: 123\n",
                encoding="utf-8",
            )
            result = install.merge_personalities_config(cfg, [])
            self.assertEqual(result, "skipped_empty")
            text = cfg.read_text(encoding="utf-8")
            self.assertIn("model: gpt-test", text)
            self.assertIn("max_tokens: 123", text)
            self.assertNotIn("personalities:", text)
            self.assertEqual(len([ln for ln in text.splitlines() if ln.strip() == "agent:"]), 1)

    def test_repairs_bogus_dash_line_even_when_empty(self):
        """The '-personalities' stripper must still run with no personas to seed."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
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
            result = install.merge_personalities_config(cfg, [])
            self.assertEqual(result, "replaced")
            lines = cfg.read_text(encoding="utf-8").splitlines()
            self.assertNotIn("-personalities", lines)
            self.assertIn("max_turns: 1", [ln.strip() for ln in lines])


if __name__ == "__main__":
    unittest.main()
