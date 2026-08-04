#!/usr/bin/env python3
"""
Real end-to-end TTS latency benchmark: local Voicebox vs hosted Fish Audio.

Measures wall-clock time for a full speak, per provider, and prints a written
comparison (latency, real-time factor, VRAM freed by hosting). This is an
honest A/B — it actually runs both paths on your machine.

Local path uses the bridge CLI (voicebox_tts.py). The very first run includes
model cold-load (~seconds), so we run it once as a warm-up and report the
*median* of subsequent runs unless --cold is passed.

Usage:
  python3 scripts/bench_tts.py
  python3 scripts/bench_tts.py --runs 3 --text "Your sentence here"
  python3 scripts/bench_tts.py --fish-key sk-fish-... --fish-label jarvis
  python3 scripts/bench_tts.py --no-fish        # local only
  python3 scripts/bench_tts.py --no-local       # fish only

Requires: ffmpeg (for audio-length probing) and requests (fish path).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(HERE, "voicebox_tts.py")


def _probe_audio_seconds(path: str) -> float | None:
    """Return audio duration in seconds via ffprobe, else None."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode == 0 and out.stdout.strip():
            return float(out.stdout.strip())
    except Exception:
        pass
    return None


def bench_local(text: str, runs: int, cold: bool, base_url: str | None) -> dict:
    """Time the local Voicebox bridge for `text`. Returns stats dict."""
    cmd = [
        sys.executable, BRIDGE,
        "--text-file", "TEXTFILE",
        "--out", "OUTFILE",
    ]
    if base_url:
        cmd += ["--base-url", base_url]

    times: list[float] = []
    audio_secs: list[float] = []
    last_out = None
    for i in range(runs + (0 if cold else 1)):
        tf = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        tf.write(text); tf.close()
        out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        out.close()
        run_cmd = [c.replace("TEXTFILE", tf.name).replace("OUTFILE", out.name) for c in cmd]
        t0 = time.perf_counter()
        try:
            r = subprocess.run(run_cmd, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            print("  ! local run timed out (600s)", file=sys.stderr)
            continue
        dt = time.perf_counter() - t0
        os.unlink(tf.name)
        if r.returncode != 0 or not os.path.exists(out.name) or os.path.getsize(out.name) <= 44:
            print(f"  ! local run failed (rc={r.returncode}): {r.stderr[:200]}", file=sys.stderr)
            if os.path.exists(out.name):
                os.unlink(out.name)
            continue
        secs = _probe_audio_seconds(out.name)
        os.unlink(out.name)
        # First run is warm-up unless --cold.
        if i == 0 and not cold:
            continue
        times.append(dt)
        if secs:
            audio_secs.append(secs)
        last_out = out.name

    if not times:
        return {"ok": False, "error": "all local runs failed"}

    med = statistics.median(times)
    mean = statistics.mean(times)
    rtf = None
    if audio_secs:
        rtf = mean / statistics.median(audio_secs)  # wall / audio
    return {
        "ok": True,
        "runs": len(times),
        "median_s": round(med, 3),
        "mean_s": round(mean, 3),
        "min_s": round(min(times), 3),
        "max_s": round(max(times), 3),
        "audio_s": round(statistics.median(audio_secs), 3) if audio_secs else None,
        "rtf": round(rtf, 3) if rtf else None,  # <1 means faster than real-time
    }


def bench_fish(text: str, runs: int, fish_key: str | None, fish_label: str, fish_voice: str | None) -> dict:
    """Time the hosted Fish Audio path (uses fish_tts.py)."""
    try:
        sys.path.insert(0, HERE)
        from fish_tts import synthesize
    except Exception as e:
        return {"ok": False, "error": f"fish_tts import failed: {e}"}

    times: list[float] = []
    audio_secs: list[float] = []
    for i in range(runs):
        out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        out.close()
        t0 = time.perf_counter()
        try:
            b = synthesize(
                text, voice_id=fish_voice, api_key=fish_key,
                out_path=out.name, audio_format="wav", sample_rate=44100, timeout=120,
            )
        except Exception as e:
            print(f"  ! fish run {i+1} failed: {e}", file=sys.stderr)
            if os.path.exists(out.name):
                os.unlink(out.name)
            continue
        dt = time.perf_counter() - t0
        if not b or os.path.getsize(out.name) <= 44:
            print(f"  ! fish run {i+1} produced no audio", file=sys.stderr)
            os.unlink(out.name)
            continue
        secs = _probe_audio_seconds(out.name)
        os.unlink(out.name)
        times.append(dt)
        if secs:
            audio_secs.append(secs)

    if not times:
        return {"ok": False, "error": "all fish runs failed"}

    med = statistics.median(times)
    mean = statistics.mean(times)
    rtf = None
    if audio_secs:
        rtf = mean / statistics.median(audio_secs)
    return {
        "ok": True,
        "runs": len(times),
        "median_s": round(med, 3),
        "mean_s": round(mean, 3),
        "min_s": round(min(times), 3),
        "max_s": round(max(times), 3),
        "audio_s": round(statistics.median(audio_secs), 3) if audio_secs else None,
        "rtf": round(rtf, 3) if rtf else None,
    }


def _fmt_rtf(rtf: float | None) -> str:
    if rtf is None:
        return "n/a"
    if rtf < 1:
        return f"{rtf:.2f}x faster than real-time"
    return f"{rtf:.2f}x real-time (slower)"


def main():
    ap = argparse.ArgumentParser(description="E2E TTS latency: local Voicebox vs hosted Fish Audio")
    ap.add_argument("--text", default="Good evening, sir. This is a latency benchmark comparing the local voice engine with the hosted provider. The quick brown fox jumps over the lazy dog.")
    ap.add_argument("--runs", type=int, default=3, help="measured runs per provider (plus 1 warm-up locally unless --cold)")
    ap.add_argument("--cold", action="store_true", help="include model cold-load in local timing")
    ap.add_argument("--base-url", default=None, help="Voicebox base URL (default 127.0.0.1:17493)")
    ap.add_argument("--fish-key", default=os.environ.get("FISH_KEY"))
    ap.add_argument("--fish-label", default="jarvis")
    ap.add_argument("--fish-voice", default=None, help="explicit Fish voice id (else cached label)")
    ap.add_argument("--no-fish", action="store_true")
    ap.add_argument("--no-local", action="store_true")
    args = ap.parse_args()

    print("=" * 64)
    print("TTS E2E LATENCY BENCHMARK")
    print("=" * 64)
    print(f"Sentence ({len(args.text)} chars): {args.text!r}")
    print(f"Measured runs/provider: {args.runs}"
          + ("  [local includes cold-load]" if args.cold else "  [local has 1 warm-up run excluded]"))
    print("-" * 64)

    local = None
    fish = None

    if not args.no_local:
        print("Local Voicebox (bridge CLI):")
        local = bench_local(args.text, args.runs, args.cold, args.base_url)
        if local.get("ok"):
            print(f"  median={local['median_s']}s  mean={local['mean_s']}s  "
                  f"audio={local.get('audio_s')}s  {_fmt_rtf(local.get('rtf'))}")
        else:
            print(f"  FAILED: {local.get('error')}")

    if not args.no_fish:
        print("Hosted Fish Audio (s2.1-pro-free):")
        if not args.fish_key and not os.environ.get("FISH_KEY"):
            print("  SKIPPED: no --fish-key / FISH_KEY env")
            fish = {"ok": False, "error": "no key"}
        else:
            fish = bench_fish(args.text, args.runs, args.fish_key, args.fish_label, args.fish_voice)
            if fish.get("ok"):
                print(f"  median={fish['median_s']}s  mean={fish['mean_s']}s  "
                      f"audio={fish.get('audio_s')}s  {_fmt_rtf(fish.get('rtf'))}")
            else:
                print(f"  FAILED: {fish.get('error')}")

    print("-" * 64)
    print("COMPARISON")
    print("-" * 64)
    if local and local.get("ok") and fish and fish.get("ok"):
        dl = local["median_s"] - fish["median_s"]
        if dl > 0:
            verdict = f"Fish is {dl:.2f}s ({dl/local['median_s']*100:.0f}%) FASTER"
        elif dl < 0:
            verdict = f"Local is {abs(dl):.2f}s ({-dl/local['median_s']*100:.0f}%) FASTER"
        else:
            verdict = "roughly equal"
        print(f"  Local median : {local['median_s']}s")
        print(f"  Fish median  : {fish['median_s']}s")
        print(f"  => {verdict}")
        print(f"  Local RTF    : {_fmt_rtf(local.get('rtf'))}")
        print(f"  Fish RTF     : {_fmt_rtf(fish.get('rtf'))}")
        print("  GPU impact   : Fish uses ZERO local VRAM; local Voicebox needs ~4-7 GB")
        print("  Privacy      : Local is fully on-device; Fish audio leaves the machine")
    elif local and local.get("ok"):
        print(f"  Local only  : median {local['median_s']}s ({_fmt_rtf(local.get('rtf'))})")
    elif fish and fish.get("ok"):
        print(f"  Fish only   : median {fish['median_s']}s ({_fmt_rtf(fish.get('rtf'))})")
    else:
        print("  No successful runs to compare.")
    print("=" * 64)


if __name__ == "__main__":
    main()
