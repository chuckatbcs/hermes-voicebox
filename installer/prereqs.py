"""
Detect and provision prerequisites for Hermes Voicebox integration.

Capabilities:
  - Python 3.10+, Git, Docker (when needed)
  - Hermes Agent / Desktop via official installers
  - Voicebox API via Docker (Linux/Windows) or Windows desktop installer
  - TTS model download via Voicebox /models/* API
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


DEFAULT_BASE_URL = "http://127.0.0.1:17493"
VOICEBOX_REPO = "https://github.com/jamiepine/voicebox.git"
HERMES_INSTALL_SH = "https://hermes-agent.nousresearch.com/install.sh"
HERMES_INSTALL_PS1 = "https://hermes-agent.nousresearch.com/install.ps1"
VOICEBOX_WINDOWS_SETUP = "https://voicebox.sh/download/windows"
VOICEBOX_RELEASE_API = "https://api.github.com/repos/jamiepine/voicebox/releases/latest"

# Engines used by the desktop plugin clone UI + daily Kokoro path.
PLUGIN_ENGINES = ("kokoro", "qwen", "chatterbox", "chatterbox_turbo")
MINIMAL_ENGINES = ("kokoro",)

MODEL_PROFILES = {
    "minimal": MINIMAL_ENGINES,
    "plugin": PLUGIN_ENGINES,
    "all": None,  # every TTS model reported by /models/status
}


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    fixable: bool = False


@dataclass
class ProvisionReport:
    checks: list[CheckResult] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_check(self, name: str, ok: bool, detail: str, fixable: bool = False) -> CheckResult:
        item = CheckResult(name=name, ok=ok, detail=detail, fixable=fixable)
        self.checks.append(item)
        return item


def log(msg: str) -> None:
    print(msg, flush=True)


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def run(
    cmd: list[str],
    *,
    check: bool = False,
    capture: bool = True,
    env: dict | None = None,
    cwd: str | Path | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
    )


def http_json(url: str, method: str = "GET", payload: dict | None = None, timeout: int = 30):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace")


def voicebox_healthy(base_url: str, timeout: int = 5) -> bool:
    try:
        http_json(f"{base_url.rstrip('/')}/health", timeout=timeout)
        return True
    except Exception:
        return False


def find_python_command() -> str | None:
    is_windows = platform.system() == "Windows"
    candidates = ["py -3", "python", "python3"] if is_windows else ["python3", "python"]
    for candidate in candidates:
        parts = candidate.split()
        if not which(parts[0]):
            continue
        try:
            probe = run(
                parts + ["-c", "import sys; print(int(sys.version_info[:2] >= (3, 10)))"],
                timeout=15,
            )
            if probe.returncode == 0 and probe.stdout.strip().endswith("1"):
                return candidate
        except Exception:
            continue
    return None


def detect_hermes(hermes_dir: Path) -> CheckResult:
    hermes_cli = which("hermes")
    has_dir = hermes_dir.exists()
    desktop_hints = []
    if platform.system() == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        desktop_hints = [
            Path(local) / "Programs" / "Hermes",
            Path(local) / "hermes",
        ]
    else:
        desktop_hints = [
            Path("/usr/share/hermes-desktop"),
            Path.home() / ".local" / "share" / "hermes-desktop",
            Path("/opt/Hermes"),
        ]
    has_desktop = any(p.exists() for p in desktop_hints)
    if hermes_cli or has_dir or has_desktop:
        bits = []
        if hermes_cli:
            bits.append(f"cli={hermes_cli}")
        if has_dir:
            bits.append(f"dir={hermes_dir}")
        if has_desktop:
            bits.append("desktop=present")
        return CheckResult("hermes", True, ", ".join(bits))
    return CheckResult("hermes", False, "Hermes Agent/Desktop not detected", fixable=True)


def detect_docker() -> CheckResult:
    docker = which("docker")
    if not docker:
        return CheckResult("docker", False, "docker not on PATH", fixable=True)
    probe = run(["docker", "info"], timeout=30)
    if probe.returncode != 0:
        return CheckResult("docker", False, "docker installed but daemon not reachable", fixable=True)
    compose = run(["docker", "compose", "version"], timeout=30)
    if compose.returncode != 0:
        return CheckResult("docker", False, "docker compose plugin missing", fixable=True)
    return CheckResult("docker", True, docker)


def detect_git() -> CheckResult:
    git = which("git")
    if git:
        return CheckResult("git", True, git)
    return CheckResult("git", False, "git not on PATH", fixable=True)


def prompt_yes(question: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    ans = input(f"{question} [y/N] ").strip().lower()
    return ans in ("y", "yes")


def install_python(*, assume_yes: bool, report: ProvisionReport) -> str | None:
    existing = find_python_command()
    if existing:
        report.add_check("python", True, existing)
        return existing

    report.add_check("python", False, "Python 3.10+ missing", fixable=True)
    if not prompt_yes("Install Python 3.10+ now?", assume_yes=assume_yes):
        report.errors.append("Python 3.10+ is required.")
        return None

    system = platform.system()
    try:
        if system == "Windows":
            if which("winget"):
                log("Installing Python via winget...")
                run(
                    [
                        "winget", "install", "-e", "--id", "Python.Python.3.12",
                        "--accept-package-agreements", "--accept-source-agreements",
                    ],
                    check=False,
                    capture=False,
                    timeout=600,
                )
            elif which("choco"):
                log("Installing Python via chocolatey...")
                run(["choco", "install", "python312", "-y"], check=False, capture=False, timeout=600)
            else:
                report.errors.append(
                    "No winget/choco found. Install Python from https://www.python.org/downloads/ "
                    "with 'Add python.exe to PATH', then re-run."
                )
                return None
        else:
            if which("apt-get"):
                log("Installing python3 via apt-get (may prompt for sudo)...")
                run(["sudo", "apt-get", "update"], check=False, capture=False, timeout=300)
                run(
                    ["sudo", "apt-get", "install", "-y", "python3", "python3-pip", "python3-venv"],
                    check=False,
                    capture=False,
                    timeout=600,
                )
            elif which("dnf"):
                run(["sudo", "dnf", "install", "-y", "python3", "python3-pip"], check=False, capture=False, timeout=600)
            else:
                report.errors.append("Unsupported package manager. Install Python 3.10+ manually.")
                return None
    except Exception as exc:
        report.errors.append(f"Python install failed: {exc}")
        return None

    # Refresh PATH on Windows after winget
    py = find_python_command()
    if py:
        report.actions.append(f"Installed Python ({py})")
        return py
    report.errors.append("Python install finished but python is still not on PATH. Open a new terminal and retry.")
    return None


def install_git(*, assume_yes: bool, report: ProvisionReport) -> bool:
    check = detect_git()
    report.checks.append(check)
    if check.ok:
        return True
    if not prompt_yes("Install Git now?", assume_yes=assume_yes):
        report.errors.append("Git is required to provision Voicebox via Docker.")
        return False
    try:
        if platform.system() == "Windows":
            if which("winget"):
                run(
                    [
                        "winget", "install", "-e", "--id", "Git.Git",
                        "--accept-package-agreements", "--accept-source-agreements",
                    ],
                    check=False,
                    capture=False,
                    timeout=600,
                )
            else:
                report.errors.append("Install Git from https://git-scm.com/download/win then retry.")
                return False
        elif which("apt-get"):
            run(["sudo", "apt-get", "install", "-y", "git", "curl", "xz-utils"], check=False, capture=False, timeout=600)
        elif which("dnf"):
            run(["sudo", "dnf", "install", "-y", "git", "curl"], check=False, capture=False, timeout=600)
        else:
            report.errors.append("Install git manually, then retry.")
            return False
    except Exception as exc:
        report.errors.append(f"Git install failed: {exc}")
        return False
    ok = detect_git().ok
    if ok:
        report.actions.append("Installed Git")
    else:
        report.errors.append("Git still missing after install attempt.")
    return ok


def install_hermes(*, assume_yes: bool, report: ProvisionReport, hermes_dir: Path) -> bool:
    check = detect_hermes(hermes_dir)
    report.checks.append(check)
    if check.ok:
        return True
    if not prompt_yes("Hermes not found. Install Hermes Agent now via official installer?", assume_yes=assume_yes):
        report.errors.append(
            "Hermes is required. Install from https://hermes-agent.nousresearch.com/docs/getting-started/installation"
        )
        return False

    try:
        if platform.system() == "Windows":
            log("Running official Hermes Windows installer...")
            ps = (
                f"Set-ExecutionPolicy Bypass -Scope Process -Force; "
                f"iex (irm {HERMES_INSTALL_PS1})"
            )
            run(["powershell", "-NoProfile", "-Command", ps], check=False, capture=False, timeout=1800)
        else:
            # Ensure curl + xz for Hermes bootstrap
            if which("apt-get"):
                run(
                    ["sudo", "apt-get", "install", "-y", "curl", "xz-utils", "git", "build-essential"],
                    check=False,
                    capture=False,
                    timeout=600,
                )
            log("Running official Hermes Linux installer...")
            run(
                ["bash", "-lc", f"curl -fsSL {HERMES_INSTALL_SH} | bash"],
                check=False,
                capture=False,
                timeout=1800,
            )
            # Best-effort desktop launch/build if CLI exists
            hermes = which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")
            if Path(hermes).exists():
                log("Ensuring Hermes Desktop is available (hermes desktop)...")
                run([hermes, "desktop"], check=False, capture=False, timeout=1800)
    except Exception as exc:
        report.errors.append(f"Hermes install failed: {exc}")
        return False

    # PATH may not include ~/.local/bin yet in this process
    local_bin = Path.home() / ".local" / "bin"
    if local_bin.is_dir():
        os.environ["PATH"] = str(local_bin) + os.pathsep + os.environ.get("PATH", "")

    check2 = detect_hermes(hermes_dir)
    if check2.ok or hermes_dir.exists():
        report.actions.append("Installed/initialized Hermes")
        return True
    report.errors.append(
        "Hermes installer ran but Hermes still not detected. "
        "Open a new terminal, run `hermes`, then re-run this installer."
    )
    return False


def vendor_voicebox_dir(hermes_dir: Path) -> Path:
    return hermes_dir / "vendor" / "voicebox"


def ensure_voicebox_repo(hermes_dir: Path, report: ProvisionReport) -> Path | None:
    if not install_git(assume_yes=True, report=report):
        # install_git already recorded check; avoid duplicate when called from docker path
        pass
    if not detect_git().ok:
        return None

    dest = vendor_voicebox_dir(hermes_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if (dest / ".git").exists():
        log(f"Updating Voicebox repo at {dest}...")
        run(["git", "-C", str(dest), "pull", "--ff-only"], check=False, capture=False, timeout=300)
        return dest

    if dest.exists() and any(dest.iterdir()):
        return dest

    log(f"Cloning Voicebox into {dest}...")
    proc = run(["git", "clone", "--depth", "1", VOICEBOX_REPO, str(dest)], capture=False, timeout=600)
    if proc.returncode != 0:
        report.errors.append("Failed to clone jamiepine/voicebox")
        return None
    report.actions.append(f"Cloned Voicebox to {dest}")
    return dest


def start_voicebox_docker(hermes_dir: Path, report: ProvisionReport, *, assume_yes: bool) -> bool:
    docker = detect_docker()
    report.checks.append(docker)
    if not docker.ok:
        if platform.system() != "Windows" and which("apt-get") and assume_yes:
            log("Docker missing — attempting convenience install (get.docker.com)...")
            run(
                ["bash", "-lc", "curl -fsSL https://get.docker.com | sudo sh"],
                check=False,
                capture=False,
                timeout=1200,
            )
            run(["sudo", "usermod", "-aG", "docker", os.environ.get("USER", "")], check=False, capture=True)
            docker = detect_docker()
            report.checks.append(docker)
        if not docker.ok:
            report.errors.append(
                "Docker is required to auto-start Voicebox on this machine. "
                "Install Docker Desktop (Windows) or Docker Engine (Linux), then re-run."
            )
            return False

    repo = ensure_voicebox_repo(hermes_dir, report)
    if not repo:
        return False

    log("Starting Voicebox via docker compose (first build can take several minutes)...")
    proc = run(
        ["docker", "compose", "up", "-d", "--build"],
        cwd=repo,
        capture=False,
        timeout=7200,
    )
    if proc.returncode != 0:
        report.errors.append("docker compose up failed")
        return False
    report.actions.append("Started Voicebox with docker compose")
    return wait_for_voicebox(DEFAULT_BASE_URL, timeout_s=300, report=report)


def download_windows_voicebox_installer(cache_dir: Path, report: ProvisionReport) -> Path | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Prefer direct redirect URL; fall back to GitHub release asset.
    target = cache_dir / "Voicebox-setup.exe"
    try:
        log(f"Downloading Voicebox Windows installer → {target}")
        urllib.request.urlretrieve(VOICEBOX_WINDOWS_SETUP, target)
        if target.stat().st_size > 1_000_000:
            report.actions.append(f"Downloaded Voicebox installer ({target})")
            return target
    except Exception as exc:
        log(f"Direct download failed ({exc}); trying GitHub releases...")

    try:
        release = http_json(VOICEBOX_RELEASE_API, timeout=60)
        assets = release.get("assets") or []
        url = None
        name = None
        for asset in assets:
            n = asset.get("name") or ""
            if n.endswith("_x64-setup.exe") or n.endswith("_en-US.msi"):
                url = asset.get("browser_download_url")
                name = n
                break
        if not url:
            report.errors.append("Could not find Voicebox Windows installer asset")
            return None
        target = cache_dir / name
        urllib.request.urlretrieve(url, target)
        report.actions.append(f"Downloaded {name}")
        return target
    except Exception as exc:
        report.errors.append(f"Voicebox installer download failed: {exc}")
        return None


def start_voicebox_windows_desktop(hermes_dir: Path, report: ProvisionReport, *, assume_yes: bool) -> bool:
    installer = download_windows_voicebox_installer(hermes_dir / "cache", report)
    if not installer:
        return False
    if not prompt_yes(
        "Launch Voicebox installer now? Complete the wizard, leave Voicebox running, then return here.",
        assume_yes=assume_yes,
    ):
        report.errors.append(f"Voicebox installer saved at {installer}; run it manually, then re-run this script.")
        return False

    log(f"Launching {installer}...")
    try:
        if installer.suffix.lower() == ".msi":
            run(["msiexec", "/i", str(installer)], check=False, capture=False, timeout=None)
        else:
            run([str(installer)], check=False, capture=False, timeout=None)
    except Exception as exc:
        report.errors.append(f"Failed to launch installer: {exc}")
        return False

    report.actions.append("Launched Voicebox desktop installer")
    log("Waiting for Voicebox API on port 17493 (start the app if the wizard finished)...")
    return wait_for_voicebox(DEFAULT_BASE_URL, timeout_s=900, report=report)


def try_start_existing_voicebox(base_url: str, report: ProvisionReport) -> bool:
    if voicebox_healthy(base_url):
        report.add_check("voicebox", True, f"API healthy at {base_url}")
        return True

    # Linux user systemd unit used by some installs
    if sys.platform.startswith("linux") and which("systemctl"):
        run(["systemctl", "--user", "start", "voicebox"], check=False, capture=True, timeout=60)
        if wait_for_voicebox(base_url, timeout_s=20, report=report, quiet=True):
            report.add_check("voicebox", True, "Started via systemctl --user")
            report.actions.append("Started voicebox systemd user service")
            return True

    # Docker container already present
    if which("docker"):
        run(["docker", "start", "voicebox"], check=False, capture=True, timeout=60)
        if wait_for_voicebox(base_url, timeout_s=30, report=report, quiet=True):
            report.add_check("voicebox", True, "Started existing docker container 'voicebox'")
            report.actions.append("Started docker container voicebox")
            return True

    report.add_check("voicebox", False, f"API not reachable at {base_url}", fixable=True)
    return False


def wait_for_voicebox(
    base_url: str,
    *,
    timeout_s: int,
    report: ProvisionReport,
    quiet: bool = False,
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if voicebox_healthy(base_url):
            if not quiet:
                report.add_check("voicebox", True, f"API healthy at {base_url}")
            return True
        time.sleep(2)
    if not quiet:
        report.errors.append(f"Timed out waiting for Voicebox API at {base_url}")
    return False


def ensure_voicebox(
    *,
    hermes_dir: Path,
    base_url: str,
    assume_yes: bool,
    report: ProvisionReport,
    prefer_docker: bool = True,
) -> bool:
    if try_start_existing_voicebox(base_url, report):
        return True

    if not prompt_yes("Voicebox API is not running. Provision it now?", assume_yes=assume_yes):
        report.errors.append("Voicebox API is required. Start it, then re-run.")
        return False

    system = platform.system()
    # Prefer Docker on Linux; on Windows try desktop app first (GPU/CUDA path), Docker second.
    if system == "Windows" and not prefer_docker:
        if start_voicebox_windows_desktop(hermes_dir, report, assume_yes=assume_yes):
            return True
        log("Desktop installer path failed or timed out; trying Docker...")
        return start_voicebox_docker(hermes_dir, report, assume_yes=assume_yes)

    if detect_docker().ok or prefer_docker:
        if start_voicebox_docker(hermes_dir, report, assume_yes=assume_yes):
            return True

    if system == "Windows":
        return start_voicebox_windows_desktop(hermes_dir, report, assume_yes=assume_yes)

    report.errors.append(
        "Could not provision Voicebox automatically on Linux without Docker. "
        "Install Docker Engine, or build from source: https://voicebox.sh/linux-install"
    )
    return False


def _models_from_status(payload) -> list[dict]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        models = payload.get("models")
        if isinstance(models, list):
            return models
    return []


def resolve_models_to_download(status_payload, engines: Iterable[str] | None) -> list[str]:
    models = _models_from_status(status_payload)
    tts_models = [
        m for m in models
        if isinstance(m, dict) and not str(m.get("engine", "")).startswith("whisper")
        and "whisper" not in str(m.get("model_name", "")).lower()
    ]

    if engines is None:
        # all TTS
        return [
            m["model_name"]
            for m in tts_models
            if m.get("model_name") and not m.get("downloaded")
        ]

    wanted = set(engines)
    chosen: list[str] = []
    # Prefer one model per engine. Favor larger/"1.7B"/non-0.6 variants when multiple.
    by_engine: dict[str, list[dict]] = {}
    for m in tts_models:
        eng = m.get("engine")
        if eng in wanted:
            by_engine.setdefault(eng, []).append(m)

    def rank(m: dict) -> tuple:
        name = str(m.get("model_name", "")).lower()
        display = str(m.get("display_name", "")).lower()
        # higher is better
        score = 0
        if "1.7" in name or "1.7" in display:
            score += 30
        if "0.6" in name or "0.6" in display:
            score -= 10
        if m.get("downloaded"):
            score += 5
        size = m.get("size_mb") or 0
        return (score, size)

    for eng in engines:
        options = by_engine.get(eng) or []
        if not options:
            # Fall back to using engine name itself as download key (docs example uses "kokoro")
            chosen.append(eng)
            continue
        best = sorted(options, key=rank, reverse=True)[0]
        if not best.get("downloaded"):
            chosen.append(best["model_name"])
    return chosen


def wait_model_download(base_url: str, model_name: str, timeout_s: int = 3600) -> bool:
    """Poll /models/status until the model is downloaded (SSE optional)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            payload = http_json(f"{base_url.rstrip('/')}/models/status", timeout=30)
            for m in _models_from_status(payload):
                if m.get("model_name") == model_name and m.get("downloaded"):
                    return True
                # some APIs may key progress by engine alias
                if m.get("engine") == model_name and m.get("downloaded"):
                    return True
        except Exception:
            pass
        # Best-effort SSE peek (non-blocking-ish via short timeout)
        try:
            req = urllib.request.Request(f"{base_url.rstrip('/')}/models/progress/{model_name}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                chunk = resp.read(4096).decode("utf-8", errors="ignore")
                if "complete" in chunk and "error" not in chunk.split("complete")[0][-40:]:
                    # weak signal; still confirm via status next loop
                    pass
                if '"status": "error"' in chunk or '"status":"error"' in chunk:
                    return False
        except Exception:
            pass
        time.sleep(3)
    return False


def ensure_models(
    *,
    base_url: str,
    profile: str,
    extra_models: list[str] | None,
    assume_yes: bool,
    report: ProvisionReport,
) -> bool:
    if not voicebox_healthy(base_url):
        report.errors.append("Cannot download models; Voicebox API is down.")
        return False

    engines = MODEL_PROFILES.get(profile, PLUGIN_ENGINES)
    try:
        status = http_json(f"{base_url.rstrip('/')}/models/status", timeout=60)
    except Exception as exc:
        report.errors.append(f"Failed to query /models/status: {exc}")
        return False

    report.add_check("models/status", True, f"profile={profile}")
    needed = resolve_models_to_download(status, engines)
    if extra_models:
        for name in extra_models:
            if name not in needed:
                needed.append(name)

    if not needed:
        report.actions.append("All requested models already downloaded")
        report.add_check("models", True, "requested models present")
        return True

    log("Models to download: " + ", ".join(needed))
    if not prompt_yes(
        f"Download {len(needed)} Voicebox model(s) now? This can be multiple GB.",
        assume_yes=assume_yes,
    ):
        report.errors.append("Model download skipped; TTS may fail until models are present.")
        return False

    ok_all = True
    for name in needed:
        log(f"Triggering download: {name}")
        try:
            http_json(
                f"{base_url.rstrip('/')}/models/download",
                method="POST",
                payload={"model_name": name},
                timeout=60,
            )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            report.errors.append(f"Download trigger failed for {name}: HTTP {exc.code} {body}")
            ok_all = False
            continue
        except Exception as exc:
            report.errors.append(f"Download trigger failed for {name}: {exc}")
            ok_all = False
            continue

        log(f"Waiting for {name} to finish downloading...")
        if wait_model_download(base_url, name):
            report.actions.append(f"Downloaded model {name}")
            # Best-effort load
            try:
                http_json(
                    f"{base_url.rstrip('/')}/models/load",
                    method="POST",
                    payload={"model_name": name},
                    timeout=600,
                )
            except Exception:
                pass
        else:
            report.errors.append(f"Timed out waiting for model {name}")
            ok_all = False

    report.add_check("models", ok_all, "downloaded" if ok_all else "partial/failed")
    return ok_all


def run_prerequisite_flow(
    *,
    hermes_dir: Path,
    base_url: str = DEFAULT_BASE_URL,
    assume_yes: bool = False,
    skip_hermes: bool = False,
    skip_voicebox: bool = False,
    skip_models: bool = False,
    model_profile: str = "plugin",
    extra_models: list[str] | None = None,
    prefer_docker: bool | None = None,
) -> ProvisionReport:
    report = ProvisionReport()
    log("=== Prerequisite check & provision ===")

    py = install_python(assume_yes=assume_yes, report=report)
    if not py:
        return report

    if prefer_docker is None:
        prefer_docker = platform.system() != "Windows"

    if not skip_hermes:
        install_hermes(assume_yes=assume_yes, report=report, hermes_dir=hermes_dir)
    else:
        report.checks.append(detect_hermes(hermes_dir))

    if not skip_voicebox:
        ensure_voicebox(
            hermes_dir=hermes_dir,
            base_url=base_url,
            assume_yes=assume_yes,
            report=report,
            prefer_docker=prefer_docker,
        )
    else:
        report.add_check("voicebox", voicebox_healthy(base_url), base_url)

    if not skip_models and voicebox_healthy(base_url):
        ensure_models(
            base_url=base_url,
            profile=model_profile,
            extra_models=extra_models,
            assume_yes=assume_yes,
            report=report,
        )
    elif skip_models:
        report.actions.append("Skipped model provisioning (--skip-models)")

    log("=== Prerequisite summary ===")
    for c in report.checks:
        flag = "OK" if c.ok else "MISSING"
        log(f"  [{flag}] {c.name}: {c.detail}")
    for a in report.actions:
        log(f"  [DONE] {a}")
    for e in report.errors:
        log(f"  [ERROR] {e}")
    return report
