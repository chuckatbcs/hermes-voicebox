#!/usr/bin/env python3
"""
Voicebox API Backend Server for Hermes Desktop Integration
Provides REST endpoints on port 17493 for voice profile management,
active voice settings, and audio streaming generation.
"""

import os
import sys
import json
import uuid
import struct
import io
import shutil
import subprocess
from pathlib import Path

try:
    import uvicorn
    from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
    from fastapi.responses import Response, StreamingResponse, JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
except ImportError:
    print("FastAPI/Uvicorn not installed. Please run: pip install fastapi uvicorn numpy", file=sys.stderr)
    sys.exit(1)

# Config & Directories
HERMES_DIR = Path.home() / ".hermes"
PROFILES_DIR = HERMES_DIR / "voicebox_profiles"
UPLOADS_DIR = PROFILES_DIR / "uploads"
PROFILES_JSON = PROFILES_DIR / "profiles.json"
SETTINGS_JSON = PROFILES_DIR / "settings.json"
SAMPLES_DIR = HERMES_DIR / "voice_samples"

PROFILES_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PROFILES = [
    {
        "id": "kokoro_default",
        "name": "Kokoro Default (Preset)",
        "voice_type": "preset",
        "preset_engine": "kokoro",
        "default_engine": "kokoro",
        "description": "Fast 82M preset voice model"
    },
    {
        "id": "chuck_voice",
        "name": "Chuck's Voice",
        "voice_type": "custom",
        "preset_engine": "chatterbox",
        "default_engine": "chatterbox",
        "audio_path": str(SAMPLES_DIR / "chuck-voice.wav")
    },
    {
        "id": "amanda_voice",
        "name": "Amanda's Voice",
        "voice_type": "custom",
        "preset_engine": "qwen",
        "default_engine": "qwen",
        "audio_path": str(SAMPLES_DIR / "amanda-voice.mp3")
    }
]


def load_profiles() -> list[dict]:
    if not PROFILES_JSON.exists():
        save_profiles(DEFAULT_PROFILES)
        return DEFAULT_PROFILES
    try:
        with open(PROFILES_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_PROFILES


def save_profiles(profiles: list[dict]):
    with open(PROFILES_JSON, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2)


def load_settings() -> dict:
    if not SETTINGS_JSON.exists():
        default_settings = {"voice_id": "kokoro_default"}
        save_settings(default_settings)
        return default_settings
    try:
        with open(SETTINGS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"voice_id": "kokoro_default"}


def save_settings(settings: dict):
    with open(SETTINGS_JSON, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def generate_wav_sine_fallback(text: str, sample_rate: int = 24000) -> bytes:
    """Generate synthesized PCM WAV audio for test/fallback generation."""
    duration = max(1.0, min(15.0, len(text) * 0.06))
    num_samples = int(sample_rate * duration)
    pcm = bytearray()
    
    freq = 220.0
    for i in range(num_samples):
        t = i / sample_rate
        # Envelope to avoid clicks
        env = min(1.0, min(t * 10, (duration - t) * 10))
        val = int(16000 * env * (0.6 * (t % (1/freq) * freq - 0.5) + 0.4 * (t % (1/(freq*1.5)) * (freq*1.5) - 0.5)))
        val = max(-32768, min(32767, val))
        pcm.extend(struct.pack("<h", val))

    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
        b"data", data_size
    )
    return bytes(header + pcm)


app = FastAPI(title="Voicebox API Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "Voicebox API Server"}


@app.get("/profiles")
def get_profiles():
    return load_profiles()


@app.get("/profiles/{profile_id}")
def get_profile(profile_id: str):
    profiles = load_profiles()
    for p in profiles:
        if p["id"] == profile_id:
            return p
    raise HTTPException(status_code=404, detail="Profile not found")


@app.get("/settings/active-voice")
def get_active_voice():
    settings = load_settings()
    return {"voice_id": settings.get("voice_id", "kokoro_default")}


@app.put("/settings/active-voice")
async def set_active_voice(request: Request):
    data = await request.json()
    voice_id = data.get("voice_id")
    if not voice_id:
        raise HTTPException(status_code=400, detail="Missing voice_id")
    settings = load_settings()
    settings["voice_id"] = voice_id
    save_settings(settings)
    return {"status": "success", "voice_id": voice_id}


@app.post("/settings/active-voice")
async def post_active_voice(request: Request):
    return await set_active_voice(request)


@app.post("/profiles/upload")
async def upload_profile(
    name: str = Form(...),
    default_engine: str = Form("qwen"),
    reference_text: str = Form(""),
    file: UploadFile = File(...)
):
    profile_id = f"voice_{uuid.uuid4().hex[:8]}"
    ext = Path(file.filename).suffix or ".wav"
    dest_path = UPLOADS_DIR / f"{profile_id}{ext}"
    
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    new_profile = {
        "id": profile_id,
        "name": name,
        "voice_type": "custom",
        "default_engine": default_engine,
        "preset_engine": default_engine,
        "reference_text": reference_text,
        "audio_path": str(dest_path)
    }

    profiles = load_profiles()
    profiles.append(new_profile)
    save_profiles(profiles)

    return new_profile


@app.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str):
    profiles = load_profiles()
    target = None
    remaining = []
    for p in profiles:
        if p["id"] == profile_id:
            target = p
        else:
            remaining.append(p)

    if not target:
        raise HTTPException(status_code=404, detail="Profile not found")

    if target.get("audio_path"):
        try:
            p = Path(target["audio_path"])
            if p.exists() and UPLOADS_DIR in p.parents:
                p.unlink(missing_ok=True)
        except Exception:
            pass

    save_profiles(remaining)
    
    # If active voice was deleted, reset to default
    settings = load_settings()
    if settings.get("voice_id") == profile_id:
        settings["voice_id"] = "kokoro_default"
        save_settings(settings)

    return {"status": "deleted", "profile_id": profile_id}


@app.post("/generate/stream")
async def generate_stream(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    text = body.get("text", "Hello, welcome to Voicebox.")
    profile_id = body.get("profile_id", "kokoro_default")
    engine = body.get("engine", "kokoro")

    # Generate WAV audio
    wav_bytes = generate_wav_sine_fallback(text)
    return Response(content=wav_bytes, media_type="audio/wav")


if __name__ == "__main__":
    port = int(os.environ.get("VOICEBOX_PORT", 17493))
    print(f"Starting Voicebox API Backend on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)
