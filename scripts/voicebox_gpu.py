#!/usr/bin/env python3
"""Voicebox GPU lifecycle helper for Hermes Voicebox integration.

Frees TTS VRAM via Voicebox's public unload API, optional idle unload, and
optional Voicebox stop when Hermes Desktop exits.

Commands:
  status              Show Voicebox health + loaded models
  unload              Unload loaded TTS models (keep Voicebox running)
  stop                Stop Voicebox service/container (hard free)
  start               Start Voicebox service/container
  touch               Update last-TTS activity stamp (used by TTS bridge)
  idle-check          Unload if stamp older than --minutes
  lifecycle-daemon    Long-running: idle unload + Hermes-exit stop + local control API
  config              Get/set ~/.hermes/voicebox_gpu.json

Control API (daemon only, localhost): http://127.0.0.1:17494/v1/*
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

DEFAULT_VOICEBOX_URL = "http://127.0.0.1:17493"
DEFAULT_CONTROL_HOST = "127.0.0.1"
DEFAULT_CONTROL_PORT = 17494
DEFAULT_IDLE_MINUTES = 15
DEFAULT_HERMES_GRACE_SEC = 20
ACTIVITY_FILENAME = "voicebox_tts_activity"
CONFIG_FILENAME = "voicebox_gpu.json"
CONTROL_PORT_FILENAME = "voicebox_gpu_control.port"

_INVALID = {"", "default", "undefined", "null", "none"}


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME") or os.environ.get("HERMES_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".hermes").resolve()


def voicebox_base_url(cli: str | None = None) -> str:
    if cli:
        return cli.rstrip("/")
    port = os.environ.get("VOICEBOX_PORT", "17493")
    return f"http://127.0.0.1:{port}"


def activity_path(home: Path | None = None) -> Path:
    return (home or hermes_home()) / ACTIVITY_FILENAME


def config_path(home: Path | None = None) -> Path:
    return (home or hermes_home()) / CONFIG_FILENAME


def default_config() -> dict[str, Any]:
    return {
        "stop_on_hermes_exit": True,
        "idle_unload_minutes": DEFAULT_IDLE_MINUTES,
        "idle_unload_enabled": True,
        "control_port": DEFAULT_CONTROL_PORT,
    }


def load_config(home: Path | None = None) -> dict[str, Any]:
    path = config_path(home)
    cfg = default_config()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception:
            pass
    return cfg


def save_config(updates: dict[str, Any], home: Path | None = None) -> dict[str, Any]:
    home = home or hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    cfg = load_config(home)
    cfg.update(updates)
    cfg["updated_at"] = datetime.now(timezone.utc).isoformat()
    config_path(home).write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return cfg


def touch_activity(home: Path | None = None) -> Path:
    home = home or hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    path = activity_path(home)
    path.write_text(
        json.dumps(
            {
                "touched_at": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def activity_age_seconds(home: Path | None = None) -> float | None:
    path = activity_path(home)
    if not path.is_file():
        return None
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _http_json(method: str, url: str, body: dict | None = None, timeout: float = 30.0) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return raw.decode("utf-8", errors="replace")


def _http_json_ok(method: str, url: str, body: dict | None = None, timeout: float = 30.0) -> tuple[bool, Any, str]:
    try:
        return True, _http_json(method, url, body=body, timeout=timeout), ""
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(exc)
        return False, None, f"HTTP {exc.code}: {detail or exc.reason}"
    except Exception as exc:
        return False, None, str(exc)


def get_health(base_url: str) -> dict[str, Any] | None:
    ok, data, _ = _http_json_ok("GET", f"{base_url}/health", timeout=5.0)
    return data if ok and isinstance(data, dict) else None


def list_loaded_models(base_url: str) -> list[str]:
    ok, data, _ = _http_json_ok("GET", f"{base_url}/models/status", timeout=10.0)
    if not ok or data is None:
        return []
    models: list[Any]
    if isinstance(data, dict):
        models = data.get("models") or data.get("engines") or []
        if isinstance(models, dict):
            models = [
                {"model_name": k, **(v if isinstance(v, dict) else {"loaded": bool(v)})}
                for k, v in models.items()
            ]
    elif isinstance(data, list):
        models = data
    else:
        return []

    loaded = []
    for m in models:
        if not isinstance(m, dict):
            continue
        if not m.get("loaded"):
            continue
        name = m.get("model_name") or m.get("name") or m.get("id") or m.get("engine")
        if name and str(name).lower() not in _INVALID:
            loaded.append(str(name))
    return loaded


def unload_models(base_url: str, model_names: list[str] | None = None) -> dict[str, Any]:
    """Unload specific models, or every loaded model, plus default unload."""
    targets = model_names if model_names is not None else list_loaded_models(base_url)
    results: dict[str, Any] = {"unloaded": [], "errors": [], "default": None}

    # Prefer named unload for each loaded engine.
    for name in targets:
        ok, data, err = _http_json_ok(
            "POST",
            f"{base_url}/models/{urllib.parse.quote(name, safe='')}/unload",
            timeout=60.0,
        )
        if ok:
            results["unloaded"].append(name)
        else:
            # Body form used by some Voicebox builds
            ok2, data2, err2 = _http_json_ok(
                "POST", f"{base_url}/models/unload", body={"model_name": name}, timeout=60.0
            )
            if ok2:
                results["unloaded"].append(name)
            else:
                results["errors"].append({"model": name, "error": err or err2, "detail": data or data2})

    # Always hit default unload once (frees default Qwen slot on older APIs).
    ok, data, err = _http_json_ok("POST", f"{base_url}/models/unload", timeout=60.0)
    results["default"] = {"ok": ok, "data": data, "error": err}
    return results


def _run(cmd: list[str], timeout: float = 60.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except Exception as exc:
        return 1, str(exc)


def stop_voicebox() -> dict[str, Any]:
    actions = []
    # systemd user unit (common local install)
    if sys.platform.startswith("linux"):
        code, out = _run(["systemctl", "--user", "stop", "voicebox.service"])
        actions.append({"method": "systemctl --user stop voicebox.service", "code": code, "out": out})
        if code != 0:
            code2, out2 = _run(["systemctl", "--user", "stop", "voicebox"])
            actions.append({"method": "systemctl --user stop voicebox", "code": code2, "out": out2})

    # Docker compose in vendor path
    vendor = hermes_home() / "vendor" / "voicebox"
    if (vendor / "docker-compose.yml").is_file() or (vendor / "compose.yml").is_file():
        code, out = _run(["docker", "compose", "down"], timeout=120.0)
        # run in vendor dir
        try:
            proc = subprocess.run(
                ["docker", "compose", "down"],
                cwd=str(vendor),
                capture_output=True,
                text=True,
                timeout=120.0,
                check=False,
            )
            actions.append(
                {
                    "method": f"docker compose down ({vendor})",
                    "code": proc.returncode,
                    "out": ((proc.stdout or "") + (proc.stderr or "")).strip(),
                }
            )
        except Exception as exc:
            actions.append({"method": f"docker compose down ({vendor})", "error": str(exc)})

    return {"actions": actions}


def start_voicebox() -> dict[str, Any]:
    actions = []
    if sys.platform.startswith("linux"):
        code, out = _run(["systemctl", "--user", "start", "voicebox.service"])
        actions.append({"method": "systemctl --user start voicebox.service", "code": code, "out": out})
        if code != 0:
            code2, out2 = _run(["systemctl", "--user", "start", "voicebox"])
            actions.append({"method": "systemctl --user start voicebox", "code": code2, "out": out2})
    vendor = hermes_home() / "vendor" / "voicebox"
    if vendor.is_dir():
        try:
            proc = subprocess.run(
                ["docker", "compose", "up", "-d"],
                cwd=str(vendor),
                capture_output=True,
                text=True,
                timeout=180.0,
                check=False,
            )
            actions.append(
                {
                    "method": f"docker compose up -d ({vendor})",
                    "code": proc.returncode,
                    "out": ((proc.stdout or "") + (proc.stderr or "")).strip(),
                }
            )
        except Exception as exc:
            actions.append({"method": f"docker compose up -d ({vendor})", "error": str(exc)})
    return {"actions": actions}


def hermes_desktop_running() -> bool:
    """Best-effort detect Hermes Desktop / `hermes gui`."""
    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                cmd = " ".join(proc.info.get("cmdline") or []).lower()
            except Exception:
                continue
            if name in {"hermes", "hermes.exe"}:
                return True
            if "hermes" in name and ("gui" in cmd or "electron" in cmd or "desktop" in cmd):
                return True
            if "hermes gui" in cmd or "hermes-agent/apps/desktop" in cmd:
                return True
            if name == "electron" and "hermes" in cmd:
                return True
        return False
    except ImportError:
        pass

    # Fallback: pgrep patterns (Linux/macOS)
    patterns = [
        ["pgrep", "-f", r"hermes([ -]gui|/gui|\.gui)"],
        ["pgrep", "-x", "Hermes"],
        ["pgrep", "-f", r"hermes-agent/apps/desktop"],
        ["pgrep", "-f", r"/Hermes .*electron|electron .*Hermes"],
    ]
    for cmd in patterns:
        code, _ = _run(cmd, timeout=5.0)
        if code == 0:
            return True
    return False


def idle_check(base_url: str, minutes: float, home: Path | None = None) -> dict[str, Any]:
    age = activity_age_seconds(home)
    result: dict[str, Any] = {
        "age_seconds": age,
        "threshold_seconds": minutes * 60.0,
        "action": "none",
    }

    # Check if Voicebox is actually alive before attempting anything.
    health = get_health(base_url)
    if not health:
        # Voicebox is not reachable — skip unload to avoid a tight loop of
        # failed HTTP calls that keep spinning the GPU process.
        result["action"] = "skip_unreachable"
        return result

    if age is None:
        # No TTS yet this boot — still unload if something is loaded and idle
        # never stamped (treat as idle forever once models are loaded).
        if not health.get("model_loaded"):
            result["action"] = "skip_no_activity_stamp"
            return result
        age = float("inf")
        result["age_seconds"] = None
        result["assumed_idle"] = True

    if age < minutes * 60.0:
        result["action"] = "skip_fresh"
        return result

    unloaded = unload_models(base_url)
    result["action"] = "unloaded"
    result["unload"] = unloaded
    return result


class _ControlHandler(BaseHTTPRequestHandler):
    daemon_ref: "LifecycleDaemon"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _send(self, code: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, {})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in {"/v1/status", "/status", "/"}:
            self._send(200, self.daemon_ref.status())
            return
        if self.path.rstrip("/") in {"/v1/config", "/config"}:
            self._send(200, load_config(self.daemon_ref.home))
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.rstrip("/")
        if path in {"/v1/unload", "/unload"}:
            self._send(200, self.daemon_ref.unload())
            return
        if path in {"/v1/stop", "/stop"}:
            self._send(200, self.daemon_ref.stop())
            return
        if path in {"/v1/start", "/start"}:
            self._send(200, start_voicebox())
            return
        if path in {"/v1/touch", "/touch"}:
            touch_activity(self.daemon_ref.home)
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "not_found"})

    def do_PUT(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in {"/v1/config", "/config"}:
            body = self._read_json()
            allowed = {
                k: body[k]
                for k in (
                    "stop_on_hermes_exit",
                    "idle_unload_minutes",
                    "idle_unload_enabled",
                )
                if k in body
            }
            cfg = save_config(allowed, self.daemon_ref.home)
            self.daemon_ref.reload_config()
            self._send(200, cfg)
            return
        self._send(404, {"error": "not_found"})


class LifecycleDaemon:
    def __init__(self, base_url: str, home: Path, control_port: int):
        self.base_url = base_url
        self.home = home
        self.control_port = control_port
        self.cfg = load_config(home)
        self._stop = threading.Event()
        self._hermes_was_up = hermes_desktop_running()
        self._hermes_down_since: float | None = None
        self._httpd: ThreadingHTTPServer | None = None

    def reload_config(self) -> None:
        self.cfg = load_config(self.home)

    def status(self) -> dict[str, Any]:
        health = get_health(self.base_url)
        return {
            "ok": True,
            "voicebox_url": self.base_url,
            "health": health,
            "loaded_models": list_loaded_models(self.base_url) if health else [],
            "activity_age_seconds": activity_age_seconds(self.home),
            "hermes_desktop_running": hermes_desktop_running(),
            "config": load_config(self.home),
            "control": f"http://{DEFAULT_CONTROL_HOST}:{self.control_port}",
        }

    def unload(self) -> dict[str, Any]:
        return unload_models(self.base_url)

    def stop(self) -> dict[str, Any]:
        # Soft unload first so a failed stop still frees VRAM if API is up.
        soft = unload_models(self.base_url)
        hard = stop_voicebox()
        return {"unload": soft, "stop": hard}

    def _loop(self) -> None:
        consecutive_failures = 0
        while not self._stop.wait(10.0):
            self.reload_config()
            cfg = self.cfg

            # Idle unload
            if cfg.get("idle_unload_enabled", True):
                minutes = float(cfg.get("idle_unload_minutes") or DEFAULT_IDLE_MINUTES)
                try:
                    idle_result = idle_check(self.base_url, minutes, self.home)
                    if idle_result.get("action") == "skip_unreachable":
                        consecutive_failures += 1
                    else:
                        consecutive_failures = 0
                except Exception:
                    consecutive_failures += 1
            else:
                # Still track health to know if Voicebox is up for backoff
                if not get_health(self.base_url):
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

            # If Voicebox is unreachable, back off exponentially (up to 5 min)
            # to avoid a tight crash-loop of start/unload attempts.
            if consecutive_failures >= 3:
                backoff = min(300, 2 ** (consecutive_failures - 3))  # 1s, 2s, 4s... cap 300s
                self._stop.wait(backoff)

            # Hermes exit → stop Voicebox; Hermes return → start it again
            # (Docker compose down leaves no auto-restart otherwise.)
            if cfg.get("stop_on_hermes_exit", True):
                up = hermes_desktop_running()
                if up:
                    if not self._hermes_was_up:
                        # Only try to start Voicebox if it's not already up
                        # (avoids crash-looping if Voicebox keeps failing to start).
                        if get_health(self.base_url):
                            self._hermes_was_up = True
                            self._hermes_down_since = None
                        else:
                            try:
                                start_voicebox()
                            except Exception:
                                pass
                            # Wait and verify it actually started before marking as up
                            self._stop.wait(5.0)
                            if get_health(self.base_url):
                                self._hermes_was_up = True
                                self._hermes_down_since = None
                            else:
                                consecutive_failures += 1
                    else:
                        self._hermes_was_up = True
                        self._hermes_down_since = None
                elif self._hermes_was_up:
                    if self._hermes_down_since is None:
                        self._hermes_down_since = time.time()
                    elif time.time() - self._hermes_down_since >= DEFAULT_HERMES_GRACE_SEC:
                        try:
                            self.stop()
                        except Exception:
                            pass
                        self._hermes_was_up = False
                        self._hermes_down_since = None

    def serve_forever(self) -> int:
        _ControlHandler.daemon_ref = self
        # Bind localhost only.
        self._httpd = ThreadingHTTPServer((DEFAULT_CONTROL_HOST, self.control_port), _ControlHandler)
        port_file = self.home / CONTROL_PORT_FILENAME
        self.home.mkdir(parents=True, exist_ok=True)
        port_file.write_text(str(self.control_port) + "\n", encoding="utf-8")

        # Handle SIGTERM (sent by systemd on shutdown) so we can gracefully
        # stop the HTTP server and unlink Voicebox models before exiting.
        # Without this, systemd sends SIGTERM, Python ignores it, and after
        # TimeoutStopSec systemd sends SIGKILL — the daemon never gets to
        # unload CUDA models, leaving them resident during the rest of shutdown.
        import signal

        self._is_systemd_stop = False

        def _handle_sigterm(signum: int, frame: Any) -> None:
            self._is_systemd_stop = True
            self._stop.set()
            if self._httpd:
                # shutdown() must be called from a different thread than
                # serve_forever() is running in, so fire it from a side
                # thread to avoid a deadlock.
                threading.Thread(
                    target=self._httpd.shutdown, name="httpd-shutdown", daemon=True
                ).start()

        signal.signal(signal.SIGTERM, _handle_sigterm)

        loop = threading.Thread(target=self._loop, name="voicebox-gpu-loop", daemon=True)
        loop.start()
        print(
            f"voicebox_gpu lifecycle-daemon on http://{DEFAULT_CONTROL_HOST}:{self.control_port} "
            f"(Voicebox {self.base_url})",
            flush=True,
        )
        try:
            self._httpd.serve_forever(poll_interval=0.5)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self._stop.set()
            if self._httpd:
                self._httpd.server_close()
            # On systemd SIGTERM: do a lightweight HTTP unlink of loaded
            # models (free GPU VRAM) but DO NOT call stop_voicebox(), which
            # would invoke `systemctl --user stop voicebox.service` — that
            # deadlocks during system shutdown because systemd is already
            # tearing down the user manager.  Systemd itself will stop
            # voicebox.service after this daemon exits.
            #
            # On Hermes-exit (non-systemd path): call self.stop() which does
            # the full unload + service restart cycle.
            if not self._is_systemd_stop:
                try:
                    self.stop()
                except Exception:
                    pass
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Voicebox GPU lifecycle helper")
    parser.add_argument("--base-url", default=None, help="Voicebox API base URL")
    parser.add_argument("--hermes-home", default=None, help="Override HERMES_HOME")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show health / loaded models / config")
    sub.add_parser("unload", help="Unload loaded TTS models")
    sub.add_parser("stop", help="Unload then stop Voicebox service")
    sub.add_parser("start", help="Start Voicebox service")
    sub.add_parser("touch", help="Update last-TTS activity stamp")

    p_idle = sub.add_parser("idle-check", help="Unload if idle longer than --minutes")
    p_idle.add_argument("--minutes", type=float, default=None)

    p_daemon = sub.add_parser("lifecycle-daemon", help="Idle + Hermes-exit + control API")
    p_daemon.add_argument("--port", type=int, default=None)

    p_cfg = sub.add_parser("config", help="Get or set GPU lifecycle config")
    p_cfg.add_argument("--stop-on-hermes-exit", choices=["on", "off", "true", "false", "1", "0"])
    p_cfg.add_argument("--idle-unload", choices=["on", "off", "true", "false", "1", "0"])
    p_cfg.add_argument("--idle-minutes", type=float, default=None)

    args = parser.parse_args(argv)
    if args.hermes_home:
        os.environ["HERMES_HOME"] = str(Path(args.hermes_home).expanduser().resolve())
    home = hermes_home()
    base = voicebox_base_url(args.base_url)

    if args.cmd == "status":
        health = get_health(base)
        payload = {
            "voicebox_url": base,
            "health": health,
            "loaded_models": list_loaded_models(base) if health else [],
            "activity_age_seconds": activity_age_seconds(home),
            "hermes_desktop_running": hermes_desktop_running(),
            "config": load_config(home),
        }
        print(json.dumps(payload, indent=2))
        return 0 if health else 1

    if args.cmd == "touch":
        path = touch_activity(home)
        print(json.dumps({"ok": True, "path": str(path)}))
        return 0

    if args.cmd == "unload":
        print(json.dumps(unload_models(base), indent=2))
        return 0

    if args.cmd == "stop":
        soft = unload_models(base)
        hard = stop_voicebox()
        print(json.dumps({"unload": soft, "stop": hard}, indent=2))
        return 0

    if args.cmd == "start":
        print(json.dumps(start_voicebox(), indent=2))
        return 0

    if args.cmd == "idle-check":
        cfg = load_config(home)
        minutes = args.minutes if args.minutes is not None else float(cfg.get("idle_unload_minutes") or DEFAULT_IDLE_MINUTES)
        print(json.dumps(idle_check(base, minutes, home), indent=2))
        return 0

    if args.cmd == "config":
        updates: dict[str, Any] = {}

        def _bool(val: str) -> bool:
            return val in {"on", "true", "1"}

        if args.stop_on_hermes_exit is not None:
            updates["stop_on_hermes_exit"] = _bool(args.stop_on_hermes_exit)
        if args.idle_unload is not None:
            updates["idle_unload_enabled"] = _bool(args.idle_unload)
        if args.idle_minutes is not None:
            updates["idle_unload_minutes"] = float(args.idle_minutes)
        cfg = save_config(updates, home) if updates else load_config(home)
        print(json.dumps(cfg, indent=2))
        return 0

    if args.cmd == "lifecycle-daemon":
        cfg = load_config(home)
        port = int(args.port or cfg.get("control_port") or DEFAULT_CONTROL_PORT)
        save_config({"control_port": port}, home)
        try:
            return LifecycleDaemon(base, home, port).serve_forever()
        except OSError as exc:
            print(f"ERROR: cannot bind {DEFAULT_CONTROL_HOST}:{port}: {exc}", file=sys.stderr)
            return 1

    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
