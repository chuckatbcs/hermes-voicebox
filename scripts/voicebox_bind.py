#!/usr/bin/env python3
"""
Bind a Voicebox voice (and optional persona key) to the current Hermes profile home.

Writes:
  - $HERMES_HOME/voicebox_binding.json
  - surgically updates tts.providers.voicebox.voice in $HERMES_HOME/config.yaml

Home resolution: HERMES_HOME → HERMES_DIR → ~/.hermes
Use with: hermes -p <profile> … or HERMES_HOME=~/.hermes/profiles/<name>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

MARKER_BEGIN = "# BEGIN hermes-voicebox"
MARKER_END = "# END hermes-voicebox"
BINDING_FILENAME = "voicebox_binding.json"
LEGACY_SIDECAR = "voicebox_active_voice.json"

_INVALID_VOICE_IDS = {
    "",
    "default",
    "undefined",
    "null",
    "none",
    "00000000-0000-0000-0000-000000000000",
}


def hermes_home(cli_home: str | None = None) -> Path:
    if cli_home:
        return Path(cli_home).expanduser().resolve()
    env = os.environ.get("HERMES_HOME") or os.environ.get("HERMES_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".hermes").resolve()


def profile_key_for_home(home: Path) -> str:
    """Map a Hermes home path to the Desktop profile key (default | <name>)."""
    home = home.resolve()
    root = (Path.home() / ".hermes").resolve()
    if home == root:
        return "default"
    profiles = root / "profiles"
    try:
        home.relative_to(profiles)
        return home.name
    except ValueError:
        return home.name or "default"


def is_valid_voice_id(voice_id: str | None) -> bool:
    vid = str(voice_id or "").strip()
    return bool(vid) and vid.lower() not in _INVALID_VOICE_IDS


def binding_path(home: Path) -> Path:
    return home / BINDING_FILENAME


def read_binding(home: Path | None = None) -> dict:
    home = home or hermes_home()
    path = binding_path(home)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    # Legacy sidecar (voice_id only)
    legacy = home / LEGACY_SIDECAR
    if legacy.is_file():
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                vid = data.get("voice_id") or data.get("profile_id") or data.get("id") or ""
                if is_valid_voice_id(vid):
                    return {"voice_id": str(vid).strip()}
        except Exception:
            pass
    return {}


def write_binding(
    voice_id: str,
    *,
    persona_key: str | None = None,
    home: Path | None = None,
) -> Path:
    home = home or hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    payload = {
        "voice_id": str(voice_id).strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if persona_key:
        payload["persona_key"] = str(persona_key).strip()
    path = binding_path(home)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    # Keep legacy sidecar in sync for older bridge installs.
    (home / LEGACY_SIDECAR).write_text(
        json.dumps({"voice_id": payload["voice_id"]}) + "\n",
        encoding="utf-8",
    )
    return path


def set_tts_voice_in_config(config_path: Path, voice_id: str) -> str:
    """
    Surgically set tts.providers.voicebox.voice without rewriting the whole YAML.
    Returns: updated | appended_voice | created_block | missing_config
    """
    voice_id = str(voice_id).strip()
    if not config_path.is_file():
        return "missing_config"

    text = config_path.read_text(encoding="utf-8")
    bak = config_path.with_suffix(config_path.suffix + ".bak")
    if not bak.exists():
        bak.write_text(text, encoding="utf-8")

    def _replace_voice_line(block: str) -> tuple[str, int]:
        return re.subn(
            r"^([ \t]*voice:[ \t]*).*$",
            rf"\g<1>{voice_id}",
            block,
            count=1,
            flags=re.MULTILINE,
        )

    begin = text.find(MARKER_BEGIN)
    end = text.find(MARKER_END)
    if begin != -1 and end != -1 and end > begin:
        block = text[begin:end]
        new_block, n = _replace_voice_line(block)
        if n:
            config_path.write_text(text[:begin] + new_block + text[end:], encoding="utf-8")
            return "updated"
        # Marker block exists but no voice line — insert under voicebox provider.
        insert = re.sub(
            r"(providers:\s*\n\s*voicebox:\s*\n)",
            rf"\g<1>      voice: {voice_id}\n",
            block,
            count=1,
        )
        if insert != block:
            config_path.write_text(text[:begin] + insert + text[end:], encoding="utf-8")
            return "appended_voice"

    # Unmarked voicebox provider
    # Use \g<n> so UUIDs that start with a digit (e.g. 5d06…) are not parsed as
    # group references like \25 when concatenated after \2.
    new_text, n = re.subn(
        r"(providers:\s*\n\s*voicebox:(?:\n[ \t]+[^\n]*)*?)\n([ \t]+voice:[ \t]*).*$",
        rf"\g<1>\n\g<2>{voice_id}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n:
        config_path.write_text(new_text, encoding="utf-8")
        return "updated"

    # Has voicebox provider but no voice key
    new_text, n = re.subn(
        r"(providers:\s*\n\s*voicebox:\s*\n)",
        rf"\g<1>      voice: {voice_id}\n",
        text,
        count=1,
    )
    if n:
        config_path.write_text(new_text, encoding="utf-8")
        return "appended_voice"

    # Append a minimal marked block (bridge path unknown — voice only; installer owns command)
    snippet = (
        f"\n{MARKER_BEGIN}\n"
        "tts:\n"
        "  provider: voicebox\n"
        "  providers:\n"
        "    voicebox:\n"
        "      type: command\n"
        f"      voice: {voice_id}\n"
        "      output_format: wav\n"
        f"{MARKER_END}\n"
    )
    config_path.write_text(text.rstrip() + snippet, encoding="utf-8")
    return "created_block"


def bind_voice(
    voice_id: str,
    *,
    persona_key: str | None = None,
    home: Path | None = None,
    update_config: bool = True,
) -> dict:
    if not is_valid_voice_id(voice_id):
        raise ValueError(f"Invalid voice id: {voice_id!r}")
    home = home or hermes_home()
    path = write_binding(voice_id, persona_key=persona_key, home=home)
    result = {"home": str(home), "binding": str(path), "voice_id": str(voice_id).strip()}
    if persona_key:
        result["persona_key"] = persona_key
    if update_config:
        cfg = home / "config.yaml"
        result["config"] = set_tts_voice_in_config(cfg, voice_id)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bind a Voicebox voice to the current Hermes profile home."
    )
    parser.add_argument("--voice", required=True, help="Voicebox profile UUID")
    parser.add_argument("--persona-key", default=None, help="Optional Hermes personality key")
    parser.add_argument(
        "--hermes-home",
        default=None,
        help="Hermes home override (default: HERMES_HOME / HERMES_DIR / ~/.hermes)",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Only write voicebox_binding.json; do not edit config.yaml",
    )
    args = parser.parse_args(argv)
    try:
        home = hermes_home(args.hermes_home)
        info = bind_voice(
            args.voice,
            persona_key=args.persona_key,
            home=home,
            update_config=not args.no_config,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
