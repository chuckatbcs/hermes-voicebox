#!/usr/bin/env python3
"""
Voicebox API Backend Server for Hermes Desktop Integration
Provides REST endpoints on port 17493 for voice profile management,
active voice settings, and Chatterbox TTS GPU voice cloning.
"""

import os
import sys
import json
import uuid
import struct
import io
import shutil
import asyncio
import tempfile
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
SAMPLES_DIR = Path(r"H:\Documents\AI Folder\Projects\Voice clone install scripts from Hermes\voice-samples")

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
        "name": "Chuck's Voice (Cloned)",
        "voice_type": "custom",
        "preset_engine": "chatterbox",
        "default_engine": "chatterbox",
        "audio_path": str(SAMPLES_DIR / "chuck-voice.wav")
    },
    {
        "id": "amanda_voice",
        "name": "Amanda's Voice (Cloned)",
        "voice_type": "custom",
        "preset_engine": "chatterbox",
        "default_engine": "chatterbox",
        "audio_path": str(SAMPLES_DIR / "amanda-voice.mp3")
    }
]

_chatterbox_model = None


def get_chatterbox_model():
    global _chatterbox_model
    if _chatterbox_model is None:
        import torch
        from chatterbox.tts import ChatterboxTTS
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Loading ChatterboxTTS model on {device}...")
        _chatterbox_model = ChatterboxTTS.from_pretrained(device=device)
    return _chatterbox_model


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
        default_settings = {"voice_id": "chuck_voice"}
        save_settings(default_settings)
        return default_settings
    try:
        with open(SETTINGS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"voice_id": "chuck_voice"}


def save_settings(settings: dict):
    with open(SETTINGS_JSON, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


async def generate_speech_wav(text: str, profile_id: str = "chuck_voice") -> bytes:
    """Generate speech audio using Chatterbox voice cloning on GPU or Edge Neural fallback."""
    profiles = load_profiles()
    profile = next((p for p in profiles if p["id"] == profile_id), None)

    # 1. Chatterbox Voice Cloning on GPU
    audio_path = profile.get("audio_path") if profile else None
    if audio_path and os.path.exists(audio_path):
        try:
            import torchaudio as ta
            loop = asyncio.get_running_loop()
            model = await loop.run_in_executor(None, get_chatterbox_model)
            print(f"Generating Chatterbox cloned voice for '{profile_id}' using reference audio: {audio_path}")

            def _run_chatterbox():
                return model.generate(text, audio_prompt_path=audio_path, cfg_weight=0.5)

            wav = await loop.run_in_executor(None, _run_chatterbox)

            wav_fd, wav_path = tempfile.mkstemp(suffix=".wav")
            os.close(wav_fd)
            await loop.run_in_executor(None, ta.save, wav_path, wav, model.sr)

            with open(wav_path, "rb") as f:
                wav_bytes = f.read()

            try:
                os.unlink(wav_path)
            except Exception:
                pass

            if len(wav_bytes) > 100:
                return wav_bytes
        except Exception as exc:
            print(f"Chatterbox voice cloning error: {exc}", file=sys.stderr)

    # 2. Edge Neural TTS Fallback
    pid = (profile_id or "").lower()
    voice_name = "en-US-ChristopherNeural"
    if "chuck" in pid:
        voice_name = "en-US-GuyNeural"
    elif "amanda" in pid:
        voice_name = "en-US-AriaNeural"

    try:
        import edge_tts
        mp3_fd, mp3_path = tempfile.mkstemp(suffix=".mp3")
        os.close(mp3_fd)
        wav_fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(wav_fd)

        communicate = edge_tts.Communicate(text, voice_name)
        await communicate.save(mp3_path)

        subprocess.run(
            ["ffmpeg", "-i", mp3_path, "-ac", "1", "-ar", "24000", "-y", wav_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        with open(wav_path, "rb") as f:
            wav_bytes = f.read()

        try:
            os.unlink(mp3_path)
            os.unlink(wav_path)
        except Exception:
            pass

        if len(wav_bytes) > 100:
            return wav_bytes
    except Exception as exc:
        print(f"Edge-TTS synthesis error: {exc}", file=sys.stderr)

    # 3. Windows SAPI Fallback
    try:
        import win32com.client
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        sapi_voice = win32com.client.Dispatch("SAPI.SpVoice")
        wav_fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(wav_fd)

        stream.Open(wav_path, 3, False)
        sapi_voice.AudioOutputStream = stream
        sapi_voice.Speak(text)
        stream.Close()

        with open(wav_path, "rb") as f:
            wav_bytes = f.read()

        try:
            os.unlink(wav_path)
        except Exception:
            pass

        if len(wav_bytes) > 100:
            return wav_bytes
    except Exception as exc:
        print(f"SAPI synthesis error: {exc}", file=sys.stderr)

    return b""


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
    return {"voice_id": settings.get("voice_id", "chuck_voice")}


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
    default_engine: str = Form("chatterbox"),
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
        "default_engine": "chatterbox",
        "preset_engine": "chatterbox",
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

    settings = load_settings()
    if settings.get("voice_id") == profile_id:
        settings["voice_id"] = "chuck_voice"
        save_settings(settings)

    return {"status": "deleted", "profile_id": profile_id}


@app.post("/generate/stream")
async def generate_stream(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    text = body.get("text", "Hello, welcome to Voicebox.")
    profile_id = body.get("profile_id") or load_settings().get("voice_id", "chuck_voice")

    wav_bytes = await generate_speech_wav(text, profile_id=profile_id)
    return Response(content=wav_bytes, media_type="audio/wav")


if __name__ == "__main__":
    port = int(os.environ.get("VOICEBOX_PORT", 17493))
    print(f"Starting Voicebox API Backend on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)
