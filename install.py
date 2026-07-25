#!/usr/bin/env python3
"""
Cross-platform installer for Hermes Voicebox integration.

Installs the desktop plugin + TTS bridge into the local Hermes directory
and (optionally) merges the voicebox TTS provider into config.yaml.

Works on Windows and Linux. Override target with HERMES_DIR.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
from pathlib import Path


MARKER_BEGIN = "# BEGIN hermes-voicebox"
MARKER_END = "# END hermes-voicebox"
PLUGIN_ID = "voice-switcher"


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def default_hermes_dir() -> Path:
    override = os.environ.get("HERMES_DIR")
    if override:
        return Path(override).expanduser().resolve()
    home = Path.home()
    return (home / ".hermes").resolve()


def find_python_command() -> str:
    """Return a command Hermes can use to run the bridge on this OS."""
    is_windows = platform.system() == "Windows"
    candidates = []
    if is_windows:
        candidates.extend(["py -3", "python", "python3"])
    else:
        candidates.extend(["python3", "python"])

    for candidate in candidates:
        parts = candidate.split()
        exe = shutil.which(parts[0])
        if not exe:
            continue
        # Prefer real interpreters; skip Windows Store alias stubs when possible.
        try:
            import subprocess

            probe = subprocess.run(
                parts + ["-c", "import sys; print(sys.version_info[:2] >= (3, 10))"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if probe.returncode == 0 and probe.stdout.strip().endswith("True"):
                return candidate
        except Exception:
            continue

    # Fallback: whatever we were launched with
    return "python" if is_windows else "python3"


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
        f"{MARKER_END}\n"
    )


def install_files(src_root: Path, hermes_dir: Path) -> tuple[Path, Path]:
    plugin_src = src_root / "desktop-plugin" / "plugin.js"
    bridge_src = src_root / "scripts" / "voicebox_tts.py"

    if not plugin_src.is_file():
        raise FileNotFoundError(f"Missing plugin source: {plugin_src}")
    if not bridge_src.is_file():
        raise FileNotFoundError(f"Missing bridge source: {bridge_src}")

    plugin_dst_dir = hermes_dir / "desktop-plugins" / PLUGIN_ID
    scripts_dst_dir = hermes_dir / "scripts"
    plugin_dst_dir.mkdir(parents=True, exist_ok=True)
    scripts_dst_dir.mkdir(parents=True, exist_ok=True)

    plugin_dst = plugin_dst_dir / "plugin.js"
    bridge_dst = scripts_dst_dir / "voicebox_tts.py"

    shutil.copy2(plugin_src, plugin_dst)
    shutil.copy2(bridge_src, bridge_dst)

    if platform.system() != "Windows":
        bridge_dst.chmod(bridge_dst.stat().st_mode | 0o111)

    return plugin_dst, bridge_dst


def merge_config(config_path: Path, snippet: str) -> str:
    """
    Merge voicebox TTS block into config.yaml.
    Returns one of: created | replaced | appended | skipped_manual
    """
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(snippet, encoding="utf-8")
        return "created"

    original = config_path.read_text(encoding="utf-8")
    backup = config_path.with_suffix(config_path.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")

    if MARKER_BEGIN in original and MARKER_END in original:
        pre = original.split(MARKER_BEGIN, 1)[0].rstrip()
        post = original.split(MARKER_END, 1)[1].lstrip("\n")
        merged = (pre + "\n\n" if pre else "") + snippet + (("\n" + post) if post else "")
        config_path.write_text(merged if merged.endswith("\n") else merged + "\n", encoding="utf-8")
        return "replaced"

    # Already configured without markers — do not clobber unknown YAML structure.
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
    parser = argparse.ArgumentParser(description="Install Hermes Voicebox integration")
    parser.add_argument(
        "--hermes-dir",
        default=None,
        help="Target Hermes directory (default: ~/.hermes or $HERMES_DIR)",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Only copy files; do not modify config.yaml",
    )
    parser.add_argument(
        "--print-snippet",
        action="store_true",
        help="Print the config snippet and exit without installing",
    )
    args = parser.parse_args(argv)

    src_root = repo_root()
    hermes_dir = Path(args.hermes_dir).expanduser().resolve() if args.hermes_dir else default_hermes_dir()
    python_cmd = find_python_command()
    bridge_path = hermes_dir / "scripts" / "voicebox_tts.py"
    snippet = build_snippet(python_cmd, bridge_path)

    print(f"=== Installing Hermes Voicebox Integration ({platform.system()}) ===")
    print(f"Repo:        {src_root}")
    print(f"Hermes dir:  {hermes_dir}")
    print(f"Python cmd:  {python_cmd}")

    if args.print_snippet:
        print()
        print(snippet)
        return 0

    plugin_dst, bridge_dst = install_files(src_root, hermes_dir)
    verify_install(plugin_dst, bridge_dst)
    print(f"Installed plugin: {plugin_dst}")
    print(f"Installed bridge: {bridge_dst}")

    snippet_path = hermes_dir / "voicebox-provider.snippet.yaml"
    snippet_path.write_text(snippet, encoding="utf-8")
    print(f"Wrote snippet:    {snippet_path}")

    config_path = hermes_dir / "config.yaml"
    if args.no_config:
        print("Skipped config.yaml (--no-config).")
    else:
        result = merge_config(config_path, snippet)
        if result == "created":
            print(f"Created {config_path} with Voicebox TTS provider.")
        elif result == "replaced":
            print(f"Updated marked Voicebox block in {config_path} (backup: config.yaml.bak).")
        elif result == "appended":
            print(f"Appended Voicebox TTS provider to {config_path} (backup: config.yaml.bak).")
        else:
            print(f"Left existing {config_path} unchanged (already has TTS/voicebox settings).")
            print("Merge the snippet below manually if needed:")

    print()
    print("=== Setup Complete ===")
    print("Config snippet (absolute paths for this machine):")
    print()
    print(snippet)
    print("Next steps:")
    print("  1. Ensure Voicebox API is running at http://127.0.0.1:17493")
    print("  2. Restart Hermes Desktop so it reloads plugins + config")
    print("  3. Open the Voicebox sidebar entry")
    if platform.system() == "Windows":
        print()
        print("Windows tip: if script execution is blocked, run:")
        print('  powershell -ExecutionPolicy Bypass -File .\\install.ps1')
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
