#!/usr/bin/env python3
"""
Cross-platform installer for Hermes Voicebox integration.

1) Checks/installs prerequisites (Python, Hermes, Voicebox, TTS models)
2) Installs the desktop plugin + TTS bridge into the local Hermes directory
3) Merges the voicebox TTS provider into config.yaml

Works on Windows and Linux. Override target with HERMES_DIR.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from installer.prereqs import (
    DEFAULT_BASE_URL,
    MODEL_PROFILES,
    find_python_command,
    log,
    run_prerequisite_flow,
    seed_sample_clones,
    voicebox_healthy,
)
from installer.prereqs import ProvisionReport


MARKER_BEGIN = "# BEGIN hermes-voicebox"
MARKER_END = "# END hermes-voicebox"
PERSONA_MARKER_BEGIN = "# BEGIN hermes-voicebox-personalities"
PERSONA_MARKER_END = "# END hermes-voicebox-personalities"
STREAMER_IMPORT_BEGIN = "# BEGIN hermes-voicebox-streamer"
STREAMER_IMPORT_END = "# END hermes-voicebox-streamer"
PRODUCE_PATCH_BEGIN = "# BEGIN hermes-voicebox-prefetch"
PRODUCE_PATCH_END = "# END hermes-voicebox-prefetch"
# Hermes command TTS defaults to 120s — too short for long cloned replies on CPU.
COMMAND_TTS_TIMEOUT_SECONDS = 600
PLUGIN_ID = "voice-switcher"


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def default_hermes_dir() -> Path:
    override = os.environ.get("HERMES_DIR") or os.environ.get("HERMES_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".hermes").resolve()


def resolve_profile_hermes_dir(profile: str | None, root: Path | None = None) -> Path:
    """
    Map Hermes Desktop profile key to its HERMES_HOME.
    default / empty → root (~/.hermes); otherwise root/profiles/<name>.
    """
    root = (root or default_hermes_dir()).resolve()
    name = (profile or "").strip()
    if not name or name.lower() in {"default", "root", "."}:
        return root
    return (root / "profiles" / name).resolve()


def list_profile_hermes_dirs(root: Path | None = None) -> list[Path]:
    """Root default home plus every ~/.hermes/profiles/<name> directory."""
    root = (root or default_hermes_dir()).resolve()
    homes = [root]
    profiles = root / "profiles"
    if profiles.is_dir():
        for child in sorted(profiles.iterdir()):
            if child.is_dir():
                homes.append(child.resolve())
    return homes


def quote_for_command(path: Path) -> str:
    text = str(path)
    if " " in text or "\t" in text:
        return f'"{text}"'
    return text


def build_tts_command(python_cmd: str, bridge_path: Path) -> str:
    return (
        f"{python_cmd} {quote_for_command(bridge_path)} "
        "--text-file {input_path} --out {output_path} --voice {voice}"
    )


def build_snippet(python_cmd: str, bridge_path: Path) -> str:
    command = build_tts_command(python_cmd, bridge_path)
    return (
        f"{MARKER_BEGIN}\n"
        "tts:\n"
        "  provider: voicebox\n"
        "  providers:\n"
        "    voicebox:\n"
        "      type: command\n"
        f"      command: {command}\n"
        "      voice: default\n"
        "      output_format: wav\n"
        f"      timeout: {COMMAND_TTS_TIMEOUT_SECONDS}\n"
        f"{MARKER_END}\n"
    )


def ensure_voicebox_timeout(
    config_path: Path, timeout: int = COMMAND_TTS_TIMEOUT_SECONDS
) -> str:
    """Ensure ``tts.providers.voicebox.timeout`` is set (works without markers)."""
    if not config_path.is_file():
        return "missing"
    original = config_path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)

    # Find the indented ``voicebox:`` key (providers.voicebox), not a top-level one.
    voicebox_idx = None
    indent = ""
    for i, line in enumerate(lines):
        m = re.match(r"^([ \t]{2,})voicebox:\s*(#.*)?$", line)
        if m:
            voicebox_idx = i
            indent = m.group(1)
            break
    if voicebox_idx is None:
        return "no_voicebox"

    child_re = re.compile(rf"^{re.escape(indent)}[ \t]+\S")
    sibling_or_outdent = re.compile(rf"^(?:{re.escape(indent)}\S|\S)")
    timeout_re = re.compile(rf"^{re.escape(indent)}[ \t]+timeout\s*:")

    timeout_idx = None
    insert_at = voicebox_idx + 1
    for j in range(voicebox_idx + 1, len(lines)):
        line = lines[j]
        if not line.strip() or line.lstrip().startswith("#"):
            insert_at = j + 1
            continue
        if timeout_re.match(line):
            timeout_idx = j
            break
        if sibling_or_outdent.match(line) and not child_re.match(line):
            insert_at = j
            break
        if child_re.match(line):
            insert_at = j + 1

    # Match child indent from an existing child key, else indent + two spaces.
    child_indent = indent + "  "
    for j in range(voicebox_idx + 1, min(voicebox_idx + 12, len(lines))):
        m = re.match(rf"^({re.escape(indent)}[ \t]+)\S", lines[j])
        if m:
            child_indent = m.group(1)
            break

    new_line = f"{child_indent}timeout: {timeout}\n"
    if timeout_idx is not None:
        if re.search(rf":\s*{timeout}\s*$", lines[timeout_idx].rstrip()):
            return "unchanged"
        lines[timeout_idx] = new_line
        action = "updated"
    else:
        lines.insert(insert_at, new_line)
        action = "inserted"

    backup = config_path.with_suffix(config_path.suffix + ".bak")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")
    text = "".join(lines)
    config_path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return action


def _streamer_import_block() -> str:
    return (
        f"{STREAMER_IMPORT_BEGIN}\n"
        "try:\n"
        "    from tools import voicebox_command_streamer  # noqa: F401\n"
        "except Exception:\n"
        "    pass\n"
        f"{STREAMER_IMPORT_END}\n"
    )
def _prefetch_produce_block() -> str:
    """Indented body inside speak-stream ``_produce`` try-block (12 spaces)."""
    return f"""            {PRODUCE_PATCH_BEGIN}
            from tools.voicebox_command_streamer import produce_speak_stream_pcm

            produce_speak_stream_pcm(
                sentences_fn=_sentences,
                streamer=streamer,
                cap=cap,
                stop=stop,
                emit=lambda c: loop.call_soon_threadsafe(chunks.put_nowait, c),
                strip_md=_strip_markdown_for_tts,
                split_fn=_split_text_for_speak_stream,
            )
            {PRODUCE_PATCH_END}
"""


def _patch_speak_stream_produce(web_server_py: Path) -> str:
    """Replace serial synth loop with look-ahead prefetch (marked, idempotent).

    Uses a *literal* exact-string replacement of the stable serial loop inside
    ``_produce`` (the ``try:`` … ``for sentence in _sentences():`` … ``except``
    block), not a regex/line-scan, so it neither backtracks on large files nor
    silently drops the patch after a Hermes reformat. If the marked block is
    already present, it is replaced in place (idempotent).
    """
    original = web_server_py.read_text(encoding="utf-8")
    block = _prefetch_produce_block()

    if PRODUCE_PATCH_BEGIN in original and PRODUCE_PATCH_END in original:
        pre = original.split(PRODUCE_PATCH_BEGIN, 1)[0].rstrip()
        post = original.split(PRODUCE_PATCH_END, 1)[1].lstrip("\n")
        merged = pre + "\n" + block + post
        if merged == original:
            return "produce_unchanged"
        action = "produce_replaced"
    else:
        # The serial loop is a stable, unique string in _produce. Match it
        # exactly (including the leading 8-space indent of `try:` and the
        # trailing `except Exception as exc:` at the same indent).
        serial = (
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
            "        except Exception as exc:"
        )
        if serial not in original:
            return "produce_pattern_miss"
        # `block` is already indented to 12 spaces; wrap it so the `try:` and
        # `except` lines line up with the replaced serial block (8 spaces).
        prefetch = (
            "        try:\n"
            + block.rstrip("\n") + "\n"
            + "        except Exception as exc:"
        )
        merged = original.replace(serial, prefetch, 1)
        action = "produce_patched"

    bak = web_server_py.with_suffix(web_server_py.suffix + ".voicebox.bak")
    if not bak.exists():
        bak.write_text(original, encoding="utf-8")
    web_server_py.write_text(
        merged if merged.endswith("\n") else merged + "\n", encoding="utf-8"
    )
    return action


def install_speak_stream_hook(hermes_root: Path) -> str:
    """
    Install Voicebox speak-stream adapter into a local hermes-agent checkout.

    Enables Desktop read-aloud to speak sentence-by-sentence (faster start)
    with look-ahead synthesis so the next sentence is prepared while the
    current one plays.
    """
    agent_dir = hermes_root / "hermes-agent"
    agent_tools = agent_dir / "tools"
    streaming_py = agent_tools / "tts_streaming.py"
    web_server_py = agent_dir / "hermes_cli" / "web_server.py"
    src = Path(__file__).resolve().parent / "scripts" / "hermes_voicebox_streamer.py"
    if not src.is_file():
        return "missing_src"
    if not streaming_py.is_file():
        return "no_hermes_agent"

    dst = agent_tools / "voicebox_command_streamer.py"
    shutil.copy2(src, dst)

    original = streaming_py.read_text(encoding="utf-8")
    block = _streamer_import_block()
    if STREAMER_IMPORT_BEGIN in original and STREAMER_IMPORT_END in original:
        pre = original.split(STREAMER_IMPORT_BEGIN, 1)[0].rstrip()
        post = original.split(STREAMER_IMPORT_END, 1)[1].lstrip("\n")
        merged = pre + "\n\n" + block + (("\n" + post) if post else "")
        import_action = "replaced_import"
    else:
        merged = original.rstrip() + "\n\n" + block
        import_action = "appended_import"

    if merged != original:
        bak = streaming_py.with_suffix(streaming_py.suffix + ".voicebox.bak")
        if not bak.exists():
            bak.write_text(original, encoding="utf-8")
        streaming_py.write_text(
            merged if merged.endswith("\n") else merged + "\n", encoding="utf-8"
        )

    produce_action = "produce_skipped"
    if web_server_py.is_file():
        produce_action = _patch_speak_stream_produce(web_server_py)

    return f"{import_action}+{produce_action}"


def _yaml_literal_block(text: str, indent: int = 6) -> str:
    pad = " " * indent
    lines = text.replace("\r\n", "\n").split("\n")
    return "\n".join(pad + line if line else pad for line in lines)


def load_sample_voices(src_root: Path) -> list[dict]:
    path = src_root / "desktop-plugin" / "sample-voices.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def build_personalities_entries(samples: list[dict]) -> str:
    """YAML entries only (under agent.personalities), wrapped in markers."""
    chunks = [PERSONA_MARKER_BEGIN]
    for sample in samples:
        key = sample["key"]
        prompt = (
            f"[Voicebox sample persona: {key}]\n"
            "CRITICAL: Adopt this persona completely for this session.\n"
            "Discard prior roleplay tones from earlier turns unless the user asks to drop character.\n"
            f"{sample['personality']}"
        )
        chunks.append(f"    {key}: |")
        chunks.append(_yaml_literal_block(prompt, indent=6))
    chunks.append(PERSONA_MARKER_END)
    return "\n".join(chunks)


def build_personalities_snippet(samples: list[dict]) -> str:
    """Standalone snippet file for users / first-time configs."""
    entries = build_personalities_entries(samples)
    # Strip marker indent context into a full agent.personalities document for the .snippet file
    body = "\n".join(
        line for line in entries.splitlines()
        if line not in (PERSONA_MARKER_BEGIN, PERSONA_MARKER_END)
    )
    return (
        f"{PERSONA_MARKER_BEGIN}\n"
        "# Named personalities for /personality and Voicebox sample voices\n"
        "agent:\n"
        "  personalities:\n"
        f"{body}\n"
        f"{PERSONA_MARKER_END}\n"
    )


def _strip_persona_keys_from_tts_providers(text: str, sample_keys: set[str]) -> tuple[str, bool]:
    """
    Remove sample-persona keys wrongly nested under tts.providers.* (a known
    corruption from marker replace / Hermes config rewrite). Returns (text, changed).
    """
    if not sample_keys or "tts:" not in text:
        return text, False
    try:
        import yaml  # local import: installer already depends on PyYAML via Hermes env or std path
    except ImportError:
        return text, False
    try:
        data = yaml.safe_load(text)
    except Exception:
        return text, False
    if not isinstance(data, dict):
        return text, False
    tts = data.get("tts")
    if not isinstance(tts, dict):
        return text, False
    providers = tts.get("providers")
    if not isinstance(providers, dict):
        return text, False
    removed = [k for k in list(providers) if k in sample_keys or (
        k != "voicebox" and isinstance(providers.get(k), str)
    )]
    if not removed:
        return text, False
    stolen: dict[str, str] = {}
    for k in removed:
        val = providers.pop(k, None)
        if isinstance(val, str) and val.strip():
            stolen[k] = val
    tts["providers"] = providers
    data["tts"] = tts
    if stolen:
        agent = data.get("agent")
        if not isinstance(agent, dict):
            agent = {}
            data["agent"] = agent
        personalities = agent.get("personalities")
        if not isinstance(personalities, dict):
            personalities = {}
            agent["personalities"] = personalities
        for key, val in stolen.items():
            personalities.setdefault(key, val)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000), True


def merge_personalities_config(config_path: Path, samples: list[dict]) -> str:
    """
    Upsert sample personalities under agent.personalities without clobbering
    other agent: settings (avoid appending a second top-level agent key).
    """
    if not samples:
        # No personas to seed (demo voices are clones now). Still repair a bogus
        # bare "-personalities" line if present so a corrupt config is cleaned up.
        if config_path.exists():
            original = config_path.read_text(encoding="utf-8")
            if re.search(r"(?m)^-personalities\s*$", original):
                repaired = re.sub(r"(?m)^-personalities\s*\n?", "", original)
                config_path.write_text(
                    repaired if repaired.endswith("\n") else repaired + "\n",
                    encoding="utf-8",
                )
                return "replaced"
        return "skipped_empty"

    sample_keys = {str(s.get("key") or "") for s in samples if s.get("key")}
    entries = build_personalities_entries(samples)
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(build_personalities_snippet(samples), encoding="utf-8")
        return "created"

    original = config_path.read_text(encoding="utf-8")
    backup = config_path.with_suffix(config_path.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")

    # Repair a known corruption: a bare ``-personalities`` line after our
    # marker block (invalid YAML; Hermes then wipes config on update).
    original = re.sub(r"(?m)^-personalities\s*\n", "", original)

    # Repair personas wrongly nested under tts.providers (breaks profile updates).
    repaired, changed = _strip_persona_keys_from_tts_providers(original, sample_keys)
    if changed:
        original = repaired

    if PERSONA_MARKER_BEGIN in original and PERSONA_MARKER_END in original:
        # If markers sit under tts.providers (no agent.personalities parent),
        # drop the marked block and append a proper agent.personalities snippet.
        pre = original.split(PERSONA_MARKER_BEGIN, 1)[0].rstrip()
        between = original.split(PERSONA_MARKER_BEGIN, 1)[1].split(PERSONA_MARKER_END, 1)[0]
        post = original.split(PERSONA_MARKER_END, 1)[1].lstrip("\n")
        pre_tail = "\n".join(pre.splitlines()[-8:])
        # Markers wrapping a top-level `agent:` snippet (common after `appended`)
        # must NOT be treated as under providers — the TTS `providers:` key often
        # appears in the preceding tail and caused false repairs on every re-install.
        marked_is_agent_doc = bool(re.search(r"(?m)^agent:\s*$", between))
        under_providers = (
            not marked_is_agent_doc
            and bool(re.search(r"(?m)^[ \t]+providers:\s*$", pre_tail))
            and "agent:" not in pre_tail
            and "personalities:" not in pre_tail
        )
        if under_providers:
            cleaned = (pre + ("\n" + post if post else "\n")).rstrip() + "\n\n"
            cleaned += build_personalities_snippet(samples)
            config_path.write_text(
                cleaned if cleaned.endswith("\n") else cleaned + "\n", encoding="utf-8"
            )
            return "repaired_providers_nesting"

        # Replace marked body. If the old marked region was a full agent: doc
        # (from build_personalities_snippet), keep that shape instead of the
        # indented-entries-only form used under agent.personalities.
        if marked_is_agent_doc:
            replacement = build_personalities_snippet(samples).rstrip() + "\n"
        else:
            replacement = entries + "\n"
        merged = (pre + "\n" if pre else "") + replacement + (post if post else "")
        merged = re.sub(r"(?m)^-personalities\s*\n", "", merged)
        config_path.write_text(merged if merged.endswith("\n") else merged + "\n", encoding="utf-8")
        return "replaced"

    # After YAML rewrite stripped markers but left persona keys under providers.
    if changed:
        # Ensure agent.personalities exists via append path below if needed.
        if re.search(r"(?m)^agent:\s*$", original) and re.search(
            r"(?m)^[ \t]*personalities:\s*$", original
        ):
            pass
        elif re.search(r"(?m)^agent:\s*$", original):
            agent_hdr = re.search(r"(?m)^agent:\s*$", original)
            assert agent_hdr is not None
            insert_at = agent_hdr.end()
            block = "\n  personalities:\n" + entries + "\n"
            merged = original[:insert_at] + block + original[insert_at:]
            config_path.write_text(merged if merged.endswith("\n") else merged + "\n", encoding="utf-8")
            return "repaired_providers_nesting"
        else:
            appended = original.rstrip() + "\n\n" + build_personalities_snippet(samples)
            config_path.write_text(
                appended if appended.endswith("\n") else appended + "\n", encoding="utf-8"
            )
            return "repaired_providers_nesting"

    # Insert under existing agent.personalities: if present.
    personalities_hdr = re.search(r"(?m)^([ \t]*)personalities:\s*$", original)
    agent_hdr = re.search(r"(?m)^agent:\s*$", original)

    def _sample_keys_present(text: str) -> set[str]:
        """Keys already defined under a personalities mapping (markers may be gone)."""
        found: set[str] = set()
        for key in sample_keys:
            if not key:
                continue
            # Match `    jarvis: |` / `    jarvis: "..."` / `    jarvis:` blocks.
            if re.search(rf"(?m)^[ \t]+{re.escape(key)}:\s*(?:\||[\"'].*|[|>].*)?$", text):
                found.add(key)
        return found

    if personalities_hdr:
        present = _sample_keys_present(original)
        missing = {k for k in sample_keys if k and k not in present}
        # Hermes Desktop often strips our # BEGIN/# END comment markers on rewrite.
        # Re-inserting the full block every install duplicated keys and bloated
        # config.yaml (seen at 2.7k+ lines) — skip when all samples already exist.
        if not missing:
            return "repaired_providers_nesting" if changed else "skipped_existing"

        # Only append missing sample keys (keep markers so a later run can replace).
        missing_samples = [s for s in samples if s.get("key") in missing]
        block = build_personalities_entries(missing_samples)
        insert_at = personalities_hdr.end()
        merged = original[:insert_at] + "\n" + block + original[insert_at:]
        config_path.write_text(merged if merged.endswith("\n") else merged + "\n", encoding="utf-8")
        return "repaired_providers_nesting" if changed else "inserted_personalities"

    if agent_hdr:
        insert_at = agent_hdr.end()
        block = "\n  personalities:\n" + entries + "\n"
        merged = original[:insert_at] + block + original[insert_at:]
        config_path.write_text(merged if merged.endswith("\n") else merged + "\n", encoding="utf-8")
        return "repaired_providers_nesting" if changed else "inserted_under_agent"

    # No agent section — append a complete one (safe: nothing to clobber).
    appended = original.rstrip() + "\n\n" + build_personalities_snippet(samples)
    config_path.write_text(appended if appended.endswith("\n") else appended + "\n", encoding="utf-8")
    return "repaired_providers_nesting" if changed else "appended"


def install_files(src_root: Path, hermes_dir: Path) -> tuple[Path, Path]:
    plugin_src_dir = src_root / "desktop-plugin"
    plugin_src = plugin_src_dir / "plugin.js"
    bridge_src = src_root / "scripts" / "voicebox_tts.py"
    bind_src = src_root / "scripts" / "voicebox_bind.py"
    streamer_src = src_root / "scripts" / "hermes_voicebox_streamer.py"
    gpu_src = src_root / "scripts" / "voicebox_gpu.py"
    diagnose_src = src_root / "scripts" / "diagnose_tts.sh"

    if not plugin_src.is_file():
        raise FileNotFoundError(f"Missing plugin source: {plugin_src}")
    if not bridge_src.is_file():
        raise FileNotFoundError(f"Missing bridge source: {bridge_src}")
    if not bind_src.is_file():
        raise FileNotFoundError(f"Missing bind helper source: {bind_src}")
    if not streamer_src.is_file():
        raise FileNotFoundError(f"Missing speak-stream source: {streamer_src}")
    if not gpu_src.is_file():
        raise FileNotFoundError(f"Missing GPU lifecycle source: {gpu_src}")
    if not diagnose_src.is_file():
        raise FileNotFoundError(f"Missing diagnose script source: {diagnose_src}")

    plugin_dst_dir = hermes_dir / "desktop-plugins" / PLUGIN_ID
    scripts_dst_dir = hermes_dir / "scripts"
    plugin_dst_dir.mkdir(parents=True, exist_ok=True)
    scripts_dst_dir.mkdir(parents=True, exist_ok=True)

    plugin_dst = plugin_dst_dir / "plugin.js"
    bridge_dst = scripts_dst_dir / "voicebox_tts.py"
    bind_dst = scripts_dst_dir / "voicebox_bind.py"
    gpu_dst = scripts_dst_dir / "voicebox_gpu.py"
    diagnose_dst = scripts_dst_dir / "diagnose_tts.sh"

    # Copy plugin entry + companion modules/assets
    for src in plugin_src_dir.iterdir():
        if src.is_file() and src.suffix.lower() in {".js", ".json", ".css", ".md"}:
            shutil.copy2(src, plugin_dst_dir / src.name)

    shutil.copy2(bridge_src, bridge_dst)
    shutil.copy2(bind_src, bind_dst)
    shutil.copy2(gpu_src, gpu_dst)
    shutil.copy2(diagnose_src, diagnose_dst)
    if platform.system() != "Windows":
        for path in (bridge_dst, bind_dst, gpu_dst, diagnose_dst):
            path.chmod(path.stat().st_mode | 0o111)

    return plugin_dst, bridge_dst


SERVICE_BACKEND_BY_PLATFORM = {
    "Linux": "systemd",
    "Windows": "schtasks",
}

WINDOWS_TASK_NAME = "Hermes_Voicebox_GPU_Lifecycle"
UNIT_NAME = "voicebox-gpu-lifecycle.service"


def service_backend(system: str | None = None) -> str | None:
    """
    Name of the OS service manager used to run the GPU lifecycle daemon.

    Returns ``"systemd"`` on Linux, ``"schtasks"`` on Windows, and ``None`` on
    platforms with no supported supervisor (e.g. macOS), where the installer
    falls back to config-only and the daemon may be run manually.
    """
    return SERVICE_BACKEND_BY_PLATFORM.get(system or platform.system())


def _install_gpu_lifecycle_systemd(
    hermes_root: Path, gpu_py: Path, base_url: str | None
) -> str:
    """Linux backend: user systemd unit under ~/.config/systemd/user."""
    unit_src = (
        Path(__file__).resolve().parent
        / "installer"
        / "systemd"
        / "voicebox-gpu-lifecycle.service"
    )
    if not unit_src.is_file():
        return "missing_unit_template"

    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_dst = unit_dir / "voicebox-gpu-lifecycle.service"

    # Rewrite ExecStart to the absolute installed script path (+ optional base URL).
    base_url_args = f" --base-url {base_url}" if base_url else ""
    text = unit_src.read_text(encoding="utf-8")
    text = text.replace(
        "ExecStart=%h/.hermes/scripts/voicebox_gpu.py lifecycle-daemon",
        f"ExecStart={sys.executable} {gpu_py} --hermes-home {hermes_root}{base_url_args} lifecycle-daemon",
    )
    unit_dst.write_text(text, encoding="utf-8")

    steps = []
    for args in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", UNIT_NAME],
    ):
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)
            steps.append(f"{' '.join(args)}=>{proc.returncode}")
            if proc.returncode != 0:
                # Mirror the Windows backend: the unit is on disk and valid,
                # but the supervisor refused to load it. Common in containers
                # and WSL, where there is no user systemd bus.
                output = ((proc.stdout or "") + (proc.stderr or "")).lower()
                if any(
                    s in output
                    for s in ("failed to connect to bus", "no such file or directory")
                ):
                    return "needs_user_bus:" + ",".join(steps)
                return "unit_written:" + ",".join(steps)
        except FileNotFoundError:
            # systemctl absent entirely (minimal container, non-systemd distro).
            return "unit_written:systemctl_unavailable"
        except Exception as exc:
            steps.append(f"{' '.join(args)}: {exc}")
            return "unit_written:" + ",".join(steps)

    return "enabled:" + ",".join(steps)


def _install_gpu_lifecycle_schtasks(
    hermes_root: Path, gpu_py: Path, base_url: str | None
) -> str:
    """
    Windows backend: a per-user Scheduled Task (systemd-user analogue).

    Registers ``Hermes_Voicebox_GPU_Lifecycle`` to run the lifecycle daemon at
    logon. Mirrors the systemd path: write the definition, then hand it to the
    OS supervisor and report each step.
    """
    task_src = (
        Path(__file__).resolve().parent
        / "installer"
        / "windows"
        / "voicebox-gpu-lifecycle.xml"
    )
    if not task_src.is_file():
        return "missing_unit_template"

    base_url_args = f" --base-url {base_url}" if base_url else ""
    arguments = (
        f'"{gpu_py}" --hermes-home "{hermes_root}"{base_url_args} lifecycle-daemon'
    )

    text = task_src.read_text(encoding="utf-8")
    text = text.replace("__VOICEBOX_GPU_COMMAND__", _xml_escape(sys.executable))
    text = text.replace("__VOICEBOX_GPU_ARGUMENTS__", _xml_escape(arguments))
    text = text.replace("__VOICEBOX_GPU_USERID__", _xml_escape(_current_windows_user()))

    # Task Scheduler requires UTF-16LE with BOM for XML definitions.
    task_dir = hermes_root / "installer"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_dst = task_dir / "voicebox-gpu-lifecycle.xml"
    task_dst.write_text(text, encoding="utf-16")

    steps = []
    for args in (
        ["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"],
        ["schtasks", "/Create", "/TN", WINDOWS_TASK_NAME, "/XML", str(task_dst), "/F"],
        ["schtasks", "/Run", "/TN", WINDOWS_TASK_NAME],
    ):
        label = args[1].lstrip("/").lower()
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)
            # A missing task on the pre-emptive /Delete is expected, not fatal.
            if label == "delete":
                continue
            output = ((proc.stdout or "") + (proc.stderr or "")).strip()
            steps.append(f"schtasks {label}=>{proc.returncode}")
            if proc.returncode != 0 and label == "create":
                # Registering a task needs an elevated shell on most systems.
                # Report that distinctly so the user gets an actionable hint
                # instead of a bare failure; the daemon still runs manually.
                if "access is denied" in output.lower():
                    return "needs_elevation:" + ",".join(steps)
                return "unit_written:" + ",".join(steps)
        except FileNotFoundError:
            # schtasks missing (e.g. Wine, stripped container).
            return "unit_written:schtasks_unavailable"
        except Exception as exc:
            steps.append(f"schtasks {label}: {exc}")
            return "unit_written:" + ",".join(steps)

    return "enabled:" + ",".join(steps)


def _current_windows_user() -> str:
    """
    DOMAIN\\user (or just user) for the Scheduled Task principal.

    Without an explicit UserId, schtasks /Create against the root task folder
    fails with "Access is denied" unless the shell is elevated.
    """
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    if domain and user:
        return f"{domain}\\{user}"
    return user or "%USERNAME%"


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def install_gpu_lifecycle(
    hermes_root: Path,
    *,
    stop_on_hermes_exit: bool = True,
    idle_minutes: int = 10,
    enable: bool = True,
    base_url: str | None = None,
) -> str:
    """
    Install GPU lifecycle config + an OS service unit for the daemon.

    Service backends (see ``service_backend()``):
      * Linux   -> user systemd unit (~/.config/systemd/user)
      * Windows -> per-user Scheduled Task (Hermes_Voicebox_GPU_Lifecycle)
      * other   -> config only; run ``lifecycle-daemon`` manually

    Returns a short status string for the installer log.
    """
    # Seed config via the installed helper (or repo script during tests).
    gpu_py = hermes_root / "scripts" / "voicebox_gpu.py"
    if not gpu_py.is_file():
        return "missing_gpu_script"

    import json as _json

    # Respect an existing user-tuned idle value: only seed the default when no
    # voicebox_gpu.json is present yet. This stops a re-run of install.sh from
    # silently clobbering a deliberate idle_unload_minutes change.
    cfg_path = (hermes_root / "voicebox_gpu.json")
    if cfg_path.is_file():
        try:
            existing = _json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and "idle_unload_minutes" in existing:
                idle_minutes = int(existing["idle_unload_minutes"])
        except Exception:
            pass

    cmd = [
        sys.executable,
        str(gpu_py),
        "--hermes-home",
        str(hermes_root),
        "config",
        "--stop-on-hermes-exit",
        "on" if stop_on_hermes_exit else "off",
        "--idle-unload",
        "on",
        "--idle-minutes",
        str(idle_minutes),
    ]
    subprocess.run(cmd, check=False, capture_output=True, text=True)

    backend = service_backend()
    if backend is None or not enable:
        return "config_only" if not enable else f"config_only_unsupported_platform_{platform.system().lower()}"

    # Never enable an OS service unit that points at a throwaway --hermes-dir
    # (unit tests / smoke installs). Only wire the service for the real home.
    real_home = (Path.home() / ".hermes").resolve()
    if hermes_root.resolve() != real_home:
        return "config_only_non_default_home"

    if backend == "systemd":
        return _install_gpu_lifecycle_systemd(hermes_root, gpu_py, base_url)
    if backend == "schtasks":
        return _install_gpu_lifecycle_schtasks(hermes_root, gpu_py, base_url)
    return "config_only_unknown_backend"


def _tts_marker_is_under_personalities(text: str) -> bool:
    """True when the marked TTS block was wrongly nested under agent.personalities."""
    if MARKER_BEGIN not in text:
        return False
    pre = text.split(MARKER_BEGIN, 1)[0].rstrip()
    return bool(re.search(r"(?m)^[ \t]*personalities:\s*$", pre.splitlines()[-1] if pre else ""))


def merge_config(config_path: Path, snippet: str, *, force: bool = False) -> str:
    """
    Merge voicebox TTS block into config.yaml.
    Returns one of: created | replaced | appended | relocated | skipped_manual
    """
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(snippet, encoding="utf-8")
        return "created"

    original = config_path.read_text(encoding="utf-8")
    backup = config_path.with_suffix(config_path.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")

    if MARKER_BEGIN in original and MARKER_END in original:
        # If a prior bug parked the TTS block under personalities:, strip it and
        # append at EOF so YAML structure (and Hermes config.set) stays valid.
        if _tts_marker_is_under_personalities(original):
            pre = original.split(MARKER_BEGIN, 1)[0].rstrip()
            post = original.split(MARKER_END, 1)[1].lstrip("\n")
            cleaned = (pre + ("\n" + post if post else "\n")).rstrip() + "\n\n" + snippet
            config_path.write_text(
                cleaned if cleaned.endswith("\n") else cleaned + "\n", encoding="utf-8"
            )
            return "relocated"

        pre = original.split(MARKER_BEGIN, 1)[0].rstrip()
        post = original.split(MARKER_END, 1)[1].lstrip("\n")
        merged = (pre + "\n\n" if pre else "") + snippet + (("\n" + post) if post else "")
        config_path.write_text(merged if merged.endswith("\n") else merged + "\n", encoding="utf-8")
        return "replaced"

    if force:
        appended = original.rstrip() + "\n\n" + snippet
        config_path.write_text(appended if appended.endswith("\n") else appended + "\n", encoding="utf-8")
        return "appended"

    if "voicebox_tts.py" in original or "provider: voicebox" in original:
        return "skipped_manual"

    if "\ntts:" in f"\n{original}" or original.lstrip().startswith("tts:"):
        return "skipped_manual"

    appended = original.rstrip() + "\n\n" + snippet
    config_path.write_text(appended, encoding="utf-8")
    return "appended"


def verify_install(plugin_dst: Path, bridge_dst: Path) -> None:
    if not plugin_dst.is_file() or plugin_dst.stat().st_size == 0:
        raise RuntimeError(f"Plugin install verification failed: {plugin_dst}")
    if not bridge_dst.is_file() or bridge_dst.stat().st_size == 0:
        raise RuntimeError(f"Bridge install verification failed: {bridge_dst}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install Hermes Voicebox integration (with prerequisite provisioning)"
    )
    parser.add_argument(
        "--hermes-dir",
        default=None,
        help="Target Hermes directory (default: ~/.hermes or $HERMES_DIR / $HERMES_HOME)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Install plugin/scripts + merge TTS into one Hermes Desktop profile home "
        "(default → ~/.hermes; other names → ~/.hermes/profiles/<name>)",
    )
    parser.add_argument(
        "--all-profiles",
        action="store_true",
        help="Install plugin/scripts + merge TTS into default and every "
        "~/.hermes/profiles/* home (Desktop loads plugins per profile HERMES_HOME)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("VOICEBOX_BASE_URL", DEFAULT_BASE_URL),
        help=f"Voicebox API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Only copy files; do not modify config.yaml",
    )
    parser.add_argument(
        "--force-config",
        action="store_true",
        help="Replace/insert the marked hermes-voicebox TTS block even if config already has tts/voicebox",
    )
    parser.add_argument(
        "--print-snippet",
        action="store_true",
        help="Print the config snippet and exit without installing",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Non-interactive: auto-approve prerequisite installs/downloads",
    )
    parser.add_argument(
        "--skip-prereqs",
        action="store_true",
        help="Skip prerequisite detection/provisioning (plugin files only)",
    )
    parser.add_argument("--skip-hermes", action="store_true", help="Do not install Hermes")
    parser.add_argument("--skip-voicebox", action="store_true", help="Do not provision Voicebox")
    parser.add_argument("--skip-models", action="store_true", help="Do not download TTS models")
    parser.add_argument(
        "--skip-samples",
        action="store_true",
        help="Do not seed the canonical demo CLONE voices (reference WAVs) into Voicebox",
    )
    parser.add_argument(
        "--skip-gpu-lifecycle",
        action="store_true",
        help="Do not install/enable the Voicebox GPU lifecycle daemon (idle unload + Hermes-exit stop)",
    )
    parser.add_argument(
        "--no-stop-voicebox-on-hermes-exit",
        action="store_true",
        help="Keep Voicebox running after Hermes Desktop quits (still idle-unloads models by default)",
    )
    parser.add_argument(
        "--model-profile",
        choices=sorted(MODEL_PROFILES.keys()),
        default="plugin",
        help="Which TTS models to ensure (default: plugin = kokoro/qwen/chatterbox/turbo)",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        help="Extra model_name to download (repeatable)",
    )
    parser.add_argument(
        "--prefer-docker",
        action="store_true",
        help="Prefer Docker for Voicebox even on Windows",
    )
    parser.add_argument(
        "--prefer-desktop",
        action="store_true",
        help="Prefer Voicebox desktop installer on Windows",
    )
    args = parser.parse_args(argv)

    src_root = repo_root()
    root_hermes = Path(args.hermes_dir).expanduser().resolve() if args.hermes_dir else default_hermes_dir()
    if args.profile and args.all_profiles:
        print("ERROR: use --profile or --all-profiles, not both", file=sys.stderr)
        return 2
    hermes_dir = (
        resolve_profile_hermes_dir(args.profile, root_hermes) if args.profile else root_hermes
    )
    python_cmd = find_python_command() or ("python" if platform.system() == "Windows" else "python3")
    # Root keeps shared speak-stream / GPU lifecycle. Desktop loads plugins from each
    # profile's HERMES_HOME, so --profile / --all-profiles also copy plugin+scripts there.
    install_root = root_hermes
    bridge_path = install_root / "scripts" / "voicebox_tts.py"
    snippet = build_snippet(python_cmd, bridge_path)

    print(f"=== Installing Hermes Voicebox Integration ({platform.system()}) ===")
    print(f"Repo:         {src_root}")
    print(f"Hermes root:  {install_root}")
    if hermes_dir != install_root:
        print(f"Profile home: {hermes_dir}")
    print(f"Python cmd:   {python_cmd}")
    print(f"Voicebox:     {args.base_url}")

    if args.print_snippet:
        print()
        print(snippet)
        return 0

    if not args.skip_prereqs:
        prefer_docker = True
        if platform.system() == "Windows":
            prefer_docker = bool(args.prefer_docker) and not args.prefer_desktop
            if args.prefer_desktop:
                prefer_docker = False
            elif not args.prefer_docker:
                prefer_docker = False  # Windows default: desktop installer first

        report = run_prerequisite_flow(
            hermes_dir=install_root,
            base_url=args.base_url,
            assume_yes=args.yes,
            skip_hermes=args.skip_hermes,
            skip_voicebox=args.skip_voicebox,
            skip_models=args.skip_models,
            model_profile=args.model_profile,
            extra_models=args.model,
            prefer_docker=prefer_docker,
        )
        if report.errors and not args.yes:
            # In interactive mode, continue if plugin can still be installed, but warn.
            print("WARNING: Some prerequisites reported errors. Continuing with plugin install...")
        elif report.errors and args.yes and not args.skip_voicebox:
            # Hard-fail in automation if Voicebox never became healthy.
            if not voicebox_healthy(args.base_url) and not args.skip_models:
                print("ERROR: Prerequisites failed; aborting.", file=sys.stderr)
                return 1

        # Seed canonical demo CLONE voices so every install ships identical demos.
        if (
            not args.skip_samples
            and not args.skip_voicebox
            and voicebox_healthy(args.base_url)
        ):
            seed_report = ProvisionReport()
            if seed_sample_clones(
                args.base_url, repo_root() / "samples", seed_report
            ):
                for a in seed_report.actions:
                    log(a)
            else:
                for e in seed_report.errors:
                    print(f"WARNING: demo clone seeding: {e}", file=sys.stderr)

    if args.all_profiles:
        config_homes = list_profile_hermes_dirs(install_root)
    elif args.profile:
        hermes_dir.mkdir(parents=True, exist_ok=True)
        config_homes = [hermes_dir]
    else:
        config_homes = [install_root]

    # Always refresh root first (shared bridge path in TTS snippet + speak-stream).
    plugin_dst, bridge_dst = install_files(src_root, install_root)
    verify_install(plugin_dst, bridge_dst)
    print(f"Installed plugin: {plugin_dst}")
    print(f"Installed bridge: {bridge_dst}")
    bind_dst = install_root / "scripts" / "voicebox_bind.py"
    if bind_dst.is_file():
        print(f"Installed binder: {bind_dst}")
    gpu_dst = install_root / "scripts" / "voicebox_gpu.py"
    if gpu_dst.is_file():
        print(f"Installed GPU helper: {gpu_dst}")

    for home in config_homes:
        if home == install_root:
            continue
        home.mkdir(parents=True, exist_ok=True)
        p_dst, b_dst = install_files(src_root, home)
        verify_install(p_dst, b_dst)
        label = home.name
        print(f"[{label}] Installed plugin: {p_dst}")
        print(f"[{label}] Installed bridge: {b_dst}")

    if not args.skip_gpu_lifecycle:
        gpu_status = install_gpu_lifecycle(
            install_root,
            stop_on_hermes_exit=not args.no_stop_voicebox_on_hermes_exit,
            enable=True,
            base_url=args.base_url,
        )
        print(f"GPU lifecycle: {gpu_status}")
        if gpu_status.startswith("needs_elevation"):
            print(
                "  NOTE: registering the Scheduled Task requires an elevated shell.\n"
                "        Re-run install from an Administrator PowerShell to enable\n"
                "        automatic idle-unload, or start the daemon manually:\n"
                f"          \"{sys.executable}\" "
                f"\"{install_root / 'scripts' / 'voicebox_gpu.py'}\" lifecycle-daemon"
            )
        elif gpu_status.startswith("needs_user_bus"):
            print(
                "  NOTE: no systemd user bus (common in WSL/containers). The unit\n"
                "        was written but not started. Enable lingering with\n"
                "        'sudo loginctl enable-linger $USER' and re-run, or start\n"
                "        the daemon manually:\n"
                f"          {sys.executable} "
                f"{install_root / 'scripts' / 'voicebox_gpu.py'} lifecycle-daemon"
            )
    else:
        print("GPU lifecycle: skipped (--skip-gpu-lifecycle)")

    stream_hook = install_speak_stream_hook(install_root)
    if stream_hook == "no_hermes_agent":
        print(
            "Speak-stream hook: skipped (no hermes-agent under Hermes root). "
            "Desktop read-aloud will keep using one-shot TTS."
        )
    elif stream_hook == "missing_src":
        print("Speak-stream hook: skipped (streamer source missing).", file=sys.stderr)
    else:
        print(f"Speak-stream hook: {stream_hook} → hermes-agent/tools/voicebox_command_streamer.py")

    # Refresh snippet with possibly updated python after prereq install
    python_cmd = find_python_command() or python_cmd
    snippet = build_snippet(python_cmd, bridge_dst)
    snippet_path = install_root / "voicebox-provider.snippet.yaml"
    snippet_path.write_text(snippet, encoding="utf-8")
    print(f"Wrote snippet:    {snippet_path}")

    samples = load_sample_voices(src_root)
    persona_snippet = build_personalities_snippet(samples)
    persona_snippet_path = install_root / "voicebox-personalities.snippet.yaml"
    if persona_snippet.strip():
        persona_snippet_path.write_text(persona_snippet, encoding="utf-8")
        print(f"Wrote personas:   {persona_snippet_path}")

    if args.no_config:
        print("Skipped config.yaml (--no-config).")
    else:
        for home in config_homes:
            home.mkdir(parents=True, exist_ok=True)
            config_path = home / "config.yaml"
            label = "default" if home == install_root else home.name
            result = merge_config(config_path, snippet, force=args.force_config)
            if result == "created":
                print(f"[{label}] Created {config_path} with Voicebox TTS provider.")
            elif result == "replaced":
                print(f"[{label}] Updated marked Voicebox block in {config_path}.")
            elif result == "appended":
                print(f"[{label}] Appended Voicebox TTS provider to {config_path}.")
            elif result == "relocated":
                print(f"[{label}] Relocated Voicebox TTS block in {config_path}.")
            else:
                print(f"[{label}] Left existing TTS block in {config_path} unchanged.")

            timeout_result = ensure_voicebox_timeout(config_path)
            if timeout_result in ("inserted", "updated"):
                print(
                    f"[{label}] Set tts.providers.voicebox.timeout="
                    f"{COMMAND_TTS_TIMEOUT_SECONDS}s ({timeout_result})."
                )
            elif timeout_result == "unchanged":
                print(
                    f"[{label}] Command TTS timeout already "
                    f"{COMMAND_TTS_TIMEOUT_SECONDS}s."
                )

            if samples:
                presult = merge_personalities_config(config_path, samples)
                print(f"[{label}] Sample personalities: {presult}")

    print()
    print("=== Setup Complete ===")
    print("Config snippet (absolute paths for this machine):")
    print()
    print(snippet)
    healthy = voicebox_healthy(args.base_url)
    print("Status:")
    print(f"  Voicebox API: {'healthy' if healthy else 'NOT REACHABLE'} ({args.base_url})")
    print("Next steps:")
    if not healthy:
        print("  1. Start Voicebox (desktop app or `docker compose up -d` in ~/.hermes/vendor/voicebox)")
        print("  2. Re-run with: python install.py -y --skip-hermes")
    print("  • Restart Hermes Desktop so it reloads plugins + config (+ speak-stream hook)")
    print("  • Open the Voicebox sidebar — voice + persona bind to the active Hermes profile")
    print(
        f"  • Long read-aloud starts per sentence via speak-stream; "
        f"command timeout is {COMMAND_TTS_TIMEOUT_SECONDS}s"
    )
    print(
        "  • New Hermes profiles: python install.py --skip-prereqs --all-profiles "
        "(copies plugin into each profile’s desktop-plugins/)"
    )
    print("  • CLI bind: HERMES_HOME=~/.hermes/profiles/<name> python3 scripts/voicebox_bind.py --voice <uuid>")
    print(
        "  • After a Hermes Agent update, re-run: "
        "python install.py -y --skip-prereqs --skip-hermes "
        "(re-applies speak-stream patches under hermes-agent/)"
    )
    print(
        "  • Free GPU: plugin button, or "
        "`python3 ~/.hermes/scripts/voicebox_gpu.py unload` "
        "(idle unload + stop-on-Hermes-exit via voicebox-gpu-lifecycle)"
    )
    if platform.system() == "Windows":
        print()
        print("Windows tip: if script execution is blocked, run:")
        print('  powershell -ExecutionPolicy Bypass -File .\\install.ps1 -Yes')
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
