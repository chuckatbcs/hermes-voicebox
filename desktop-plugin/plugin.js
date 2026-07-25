import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Button, Input, Select, SelectContent, SelectItem, SelectTrigger, SelectValue, host } from '@hermes/plugin-sdk';

const DEFAULT_BACKEND_URL = 'http://127.0.0.1:17493';
const PERSONA_DEBOUNCE_MS = 400;
const MIN_SAMPLE_SECONDS = 2;
const MAX_SAMPLE_SECONDS = 120;

function resolveBackendUrl() {
  try {
    const stored = localStorage.getItem('voicebox_backend_url');
    if (stored && /^https?:\/\//i.test(stored)) return stored.replace(/\/$/, '');
  } catch (_) {}
  return DEFAULT_BACKEND_URL;
}

const BACKEND_URL = resolveBackendUrl();

// ─────────────────────────────────────────────
// Engine metadata
// ─────────────────────────────────────────────
const ENGINE_META = {
  kokoro: {
    label:      'Kokoro 82M',
    badge:      '🟢',
    vram:       '~400 MB',
    quality:    'Great (preset voices)',
    cloning:    false,
    description:'Tiny & fast. Near-zero GPU load. 50+ preset voices. No custom cloning.'
  },
  qwen: {
    label:      'Qwen TTS 1.7B',
    badge:      '🔴',
    vram:       '~7.6 GB',
    quality:    'Best (voice cloning)',
    cloning:    true,
    description:'Highest fidelity voice cloning. Uses most of your GPU. Fans will spin.'
  },
  chatterbox: {
    label:      'Chatterbox 3B',
    badge:      '🟡',
    vram:       '~4 GB',
    quality:    'Good (voice cloning)',
    cloning:    true,
    description:'Mid-size cloning model. Good quality with moderate GPU usage.'
  },
  chatterbox_turbo: {
    label:      'Chatterbox Turbo',
    badge:      '🟡',
    vram:       '~4 GB',
    quality:    'Good (English, fast)',
    cloning:    true,
    description:'Faster Chatterbox variant. English-optimised with expression tags.'
  },
};

const CLONING_ENGINE_OPTIONS = [
  { value: 'qwen',             label: 'Qwen TTS 1.7B',      badge: '🔴', vram: '~7.6 GB',  note: 'Best quality' },
  { value: 'chatterbox',       label: 'Chatterbox 3B',       badge: '🟡', vram: '~4 GB',    note: 'Good quality, moderate GPU' },
  { value: 'chatterbox_turbo', label: 'Chatterbox Turbo',    badge: '🟡', vram: '~4 GB',    note: 'Fast English cloning' },
];

// ── Helpers ────────────────────────────────────
function getVoiceEngine(voice) {
  return voice?.preset_engine || voice?.default_engine || (voice?.voice_type === 'preset' ? 'kokoro' : 'qwen');
}

function getEngineMeta(voice) {
  const eng = getVoiceEngine(voice);
  return { engine: eng, meta: ENGINE_META[eng] || { badge: '⚪', label: eng, vram: '' } };
}

function engineBadge(voice) {
  const { meta } = getEngineMeta(voice);
  return `${meta.badge} ${meta.label}`;
}

function formatDuration(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function extensionOf(filePath) {
  const base = filePath.split(/[/\\]/).pop() || '';
  const idx = base.lastIndexOf('.');
  return idx > 0 ? base.slice(idx + 1).toLowerCase() : 'wav';
}

function pickRecorderMime() {
  if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) {
    return '';
  }
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4',
    'audio/ogg;codecs=opus',
    'audio/ogg',
  ];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || '';
}

function extensionForMime(mimeType) {
  const m = (mimeType || '').toLowerCase();
  if (m.includes('webm')) return 'webm';
  if (m.includes('ogg')) return 'ogg';
  if (m.includes('mp4') || m.includes('m4a') || m.includes('aac')) return 'm4a';
  if (m.includes('wav')) return 'wav';
  return 'webm';
}

async function apiFetch(path, options = {}) {
  const res = await fetch(`${BACKEND_URL}${path}`, options);
  if (!res.ok) {
    let detail = '';
    try {
      const data = await res.json();
      detail = data.detail || data.message || '';
    } catch (_) {
      try { detail = await res.text(); } catch (_) {}
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return res.json();
  return res;
}

// ─────────────────────────────────────────────
// Voice selection persistence via Voicebox API
// instead of Hermes config store (which crashes)
// ─────────────────────────────────────────────
async function saveActiveVoice(voiceId) {
  await apiFetch('/settings/active-voice', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ voice_id: voiceId })
  });
}

async function loadActiveVoice(signal) {
  try {
    const data = await apiFetch('/settings/active-voice', signal ? { signal } : {});
    return data?.voice_id || '';
  } catch {
    return '';
  }
}

// ─────────────────────────────────────────────
// Main view
// ─────────────────────────────────────────────
function VoiceboxView() {
  const [voices, setVoices]               = useState([]);
  const [activeVoiceId, setActiveVoiceId] = useState('');
  const [cloneName, setCloneName]         = useState('');
  const [cloneEngine, setCloneEngine]     = useState('qwen');
  const [referenceText, setReferenceText] = useState('The quick brown fox jumps over the lazy dog.');
  const [deleteConfirmId, setDeleteConfirmId] = useState(null);
  const [isDeleting, setIsDeleting]       = useState(false);

  // Persona state (cache; server personality is preferred when present)
  const [personaMapping, setPersonaMapping] = useState(() => {
    try {
      const stored = localStorage.getItem('hermes_personas');
      if (stored) return JSON.parse(stored);
    } catch (e) {}
    return {
      'Default': 'You are a helpful AI assistant.',
      'Jarvis': 'You are Jarvis. Be highly formal, polite, and efficient.',
    };
  });
  const [personaDrafts, setPersonaDrafts] = useState({});

  // File / recording state
  const [fileName, setFileName]           = useState('');
  const [uploadFileName, setUploadFileName] = useState('sample.wav');
  const [audioUrl, setAudioUrl]           = useState(null);
  const [audioBlob, setAudioBlob]         = useState(null);
  const [audioDuration, setAudioDuration] = useState(null);
  const [isSaving, setIsSaving]           = useState(false);
  const [isPlaying, setIsPlaying]         = useState(false);
  const [recordingState, setRecordingState] = useState('idle'); // idle | recording | recorded
  const [recordElapsed, setRecordElapsed] = useState(0);

  // Connection & loading state
  const [isLoading, setIsLoading]         = useState(true);
  const [isConnected, setIsConnected]     = useState(null);

  const playbackAudioRef = useRef(null);
  const deleteTimerRef   = useRef(null);
  const personaTimersRef = useRef({});
  const audioObjectUrlRef = useRef(null);
  const fetchAbortRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const audioChunksRef = useRef([]);
  const recordMimeRef = useRef('');
  const recordStartedAtRef = useRef(0);
  const recordTickRef = useRef(null);
  const recordMaxTimerRef = useRef(null);

  const revokeAudioUrl = useCallback(() => {
    if (audioObjectUrlRef.current) {
      URL.revokeObjectURL(audioObjectUrlRef.current);
      audioObjectUrlRef.current = null;
    }
  }, []);

  const stopPlayback = useCallback(() => {
    if (playbackAudioRef.current) {
      playbackAudioRef.current.pause();
      playbackAudioRef.current.onended = null;
      playbackAudioRef.current = null;
    }
    setIsPlaying(false);
  }, []);

  const clearRecordTimers = useCallback(() => {
    if (recordTickRef.current) {
      clearInterval(recordTickRef.current);
      recordTickRef.current = null;
    }
    if (recordMaxTimerRef.current) {
      clearTimeout(recordMaxTimerRef.current);
      recordMaxTimerRef.current = null;
    }
  }, []);

  const releaseMediaStream = useCallback(() => {
    if (mediaStreamRef.current) {
      try {
        mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      } catch (_) {}
      mediaStreamRef.current = null;
    }
  }, []);

  const applyCapturedSample = useCallback(async (blob, nameForUi, uploadName) => {
    stopPlayback();
    revokeAudioUrl();
    const objectUrl = URL.createObjectURL(blob);
    audioObjectUrlRef.current = objectUrl;
    setAudioBlob(blob);
    setAudioUrl(objectUrl);
    setFileName(nameForUi);
    setUploadFileName(uploadName);
    setAudioDuration(null);
    setRecordingState('recorded');

    try {
      const tempAudio = new Audio(objectUrl);
      await new Promise((resolve, reject) => {
        tempAudio.onloadedmetadata = resolve;
        tempAudio.onerror = reject;
        setTimeout(() => reject(new Error('metadata timeout')), 5000);
      });
      if (isFinite(tempAudio.duration) && tempAudio.duration > 0) {
        setAudioDuration(tempAudio.duration);
      } else if (recordStartedAtRef.current) {
        setAudioDuration(Math.max(0, (Date.now() - recordStartedAtRef.current) / 1000));
      }
      tempAudio.src = '';
    } catch (durErr) {
      console.warn('Could not read audio duration:', durErr);
      if (recordStartedAtRef.current) {
        setAudioDuration(Math.max(0, (Date.now() - recordStartedAtRef.current) / 1000));
      }
    }
  }, [revokeAudioUrl, stopPlayback]);

  const finalizeRecording = useCallback(() => {
    clearRecordTimers();
    // Stop tracks before creating any blob/object URL (avoids prior Electron segfault path).
    releaseMediaStream();
    mediaRecorderRef.current = null;

    const mime = recordMimeRef.current || 'audio/webm';
    const chunks = audioChunksRef.current;
    audioChunksRef.current = [];
    if (!chunks.length) {
      setRecordingState('idle');
      host.notify({ kind: 'warning', title: 'Empty Recording', message: 'No audio was captured. Try again or select a file.' });
      return;
    }
    const blob = new Blob(chunks, { type: mime });
    const ext = extensionForMime(mime);
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const uiName = `mic-recording-${stamp}.${ext}`;
    applyCapturedSample(blob, uiName, `sample.${ext}`);
    setCloneName((prev) => (prev && prev.trim() ? prev : 'Mic Recording'));
  }, [applyCapturedSample, clearRecordTimers, releaseMediaStream]);

  const stopRecording = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === 'inactive') {
      clearRecordTimers();
      releaseMediaStream();
      setRecordingState((s) => (s === 'recording' ? 'idle' : s));
      return;
    }
    try {
      recorder.stop();
    } catch (err) {
      console.warn('MediaRecorder.stop failed:', err);
      clearRecordTimers();
      releaseMediaStream();
      setRecordingState('idle');
    }
  }, [clearRecordTimers, releaseMediaStream]);

  const startRecording = useCallback(async () => {
    if (recordingState === 'recording') return;
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      host.notify({
        kind: 'error',
        title: 'Recording Unsupported',
        message: 'Microphone capture is unavailable here. Use Select Audio Sample File instead.',
      });
      return;
    }
    if (typeof MediaRecorder === 'undefined') {
      host.notify({
        kind: 'error',
        title: 'Recording Unsupported',
        message: 'MediaRecorder is unavailable. Use Select Audio Sample File instead.',
      });
      return;
    }

    try {
      if (window.hermesDesktop?.requestMicrophoneAccess) {
        const permitted = await window.hermesDesktop.requestMicrophoneAccess();
        if (permitted === false) {
          host.notify({ kind: 'error', title: 'Mic Access Denied', message: 'Microphone access was denied.' });
          return;
        }
      }

      stopPlayback();
      clearRecordTimers();
      releaseMediaStream();
      audioChunksRef.current = [];

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          channelCount: 1,
        },
      });
      mediaStreamRef.current = stream;

      const mimeType = pickRecorderMime();
      recordMimeRef.current = mimeType || 'audio/webm';
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      recorder.onerror = (ev) => {
        console.error('MediaRecorder error', ev);
        clearRecordTimers();
        releaseMediaStream();
        mediaRecorderRef.current = null;
        setRecordingState('idle');
        host.notify({ kind: 'error', title: 'Recording Failed', message: 'Microphone recorder error. Try a file upload instead.' });
      };
      recorder.onstop = () => {
        finalizeRecording();
      };

      // timeslice keeps chunks flowing; stop tracks only in finalizeRecording
      recorder.start(250);
      recordStartedAtRef.current = Date.now();
      setRecordElapsed(0);
      setRecordingState('recording');
      setAudioDuration(null);

      recordTickRef.current = setInterval(() => {
        setRecordElapsed(Math.floor((Date.now() - recordStartedAtRef.current) / 1000));
      }, 250);

      recordMaxTimerRef.current = setTimeout(() => {
        host.notify({
          kind: 'info',
          title: 'Recording Limit',
          message: `Stopped at ${MAX_SAMPLE_SECONDS}s (Voicebox max sample length).`,
        });
        stopRecording();
      }, MAX_SAMPLE_SECONDS * 1000);
    } catch (err) {
      console.error(err);
      clearRecordTimers();
      releaseMediaStream();
      mediaRecorderRef.current = null;
      setRecordingState('idle');
      host.notify({
        kind: 'error',
        title: 'Mic Access Failed',
        message: (err && err.message) || 'Could not access microphone. Use Select Audio Sample File instead.',
      });
    }
  }, [
    clearRecordTimers,
    finalizeRecording,
    recordingState,
    releaseMediaStream,
    stopPlayback,
    stopRecording,
  ]);

  // ── Fetch profiles + active voice ──────────
  const fetchProfilesAndConfig = useCallback(async () => {
    if (fetchAbortRef.current) fetchAbortRef.current.abort();
    const controller = new AbortController();
    fetchAbortRef.current = controller;

    try {
      const data = await apiFetch('/profiles', { signal: controller.signal });
      if (controller.signal.aborted) return;
      setVoices(Array.isArray(data) ? data : []);
      setIsConnected(true);

      const savedVoice = await loadActiveVoice(controller.signal);
      if (!controller.signal.aborted && savedVoice) setActiveVoiceId(savedVoice);
    } catch (err) {
      if (err?.name === 'AbortError') return;
      console.error(err);
      setIsConnected(false);
      host.notify({ kind: 'error', title: 'Voicebox Connection Failed',
        message: 'Could not connect to Voicebox backend. Is it running?' });
    } finally {
      if (!controller.signal.aborted) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProfilesAndConfig();

    return () => {
      if (fetchAbortRef.current) fetchAbortRef.current.abort();
      if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
      Object.values(personaTimersRef.current).forEach(clearTimeout);
      personaTimersRef.current = {};
      clearRecordTimers();
      try {
        if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
          mediaRecorderRef.current.onstop = null;
          mediaRecorderRef.current.stop();
        }
      } catch (_) {}
      mediaRecorderRef.current = null;
      releaseMediaStream();
      stopPlayback();
      revokeAudioUrl();
    };
  }, [fetchProfilesAndConfig, stopPlayback, revokeAudioUrl, clearRecordTimers, releaseMediaStream]);

  // ── Auto-cancel delete confirmation after 5 seconds ──
  useEffect(() => {
    if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
    if (deleteConfirmId) {
      deleteTimerRef.current = setTimeout(() => setDeleteConfirmId(null), 5000);
    }
    return () => { if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current); };
  }, [deleteConfirmId]);

  const persistPersona = useCallback(async (voice, val) => {
    setPersonaMapping(prev => {
      const next = { ...prev, [voice.id]: val, [voice.name]: val };
      try {
        localStorage.setItem('hermes_personas', JSON.stringify(next));
      } catch (_) {}
      return next;
    });

    try {
      await apiFetch(`/profiles/${voice.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: voice.name, language: voice.language || 'en', personality: val })
      });
      setVoices(prev => prev.map(p => p.id === voice.id ? { ...p, personality: val } : p));

      if (voice.id === activeVoiceId) {
        const sessionId = host.state?.activeSessionId?.get();
        await host.request('config.set', { key: 'personality', value: val, session_id: sessionId || undefined });
      }
    } catch (err) {
      console.warn('Persona sync failed:', err);
      host.notify({
        kind: 'error',
        title: 'Persona Sync Failed',
        message: err.message || 'Could not save persona prompt.'
      });
    }
  }, [activeVoiceId]);

  const schedulePersonaPersist = useCallback((voice, val) => {
    setPersonaDrafts(prev => ({ ...prev, [voice.id]: val }));
    if (personaTimersRef.current[voice.id]) clearTimeout(personaTimersRef.current[voice.id]);
    personaTimersRef.current[voice.id] = setTimeout(() => {
      delete personaTimersRef.current[voice.id];
      persistPersona(voice, val);
    }, PERSONA_DEBOUNCE_MS);
  }, [persistPersona]);

  // ── Active Voice Selection ───────────────────
  const handleVoiceChange = async (voiceId) => {
    if (!voiceId) return;
    const previousId = activeVoiceId;
    const voice = voices.find(v => v.id === voiceId);
    const { engine, meta } = getEngineMeta(voice);
    try {
      setActiveVoiceId(voiceId);
      await saveActiveVoice(voiceId);
      host.notify({ kind: 'success', title: 'Voice Updated',
        message: `Active voice: ${voice?.name || voiceId}  •  ${meta.label || engine}  •  VRAM ${meta.vram || '?'}` });

      const voiceName = voice?.name || voiceId;
      const persona = voice?.personality || personaMapping[voiceId] || personaMapping[voiceName];

      if (!persona) {
        host.notify({ kind: 'info', title: 'Voice Changed', message: `Active voice set to "${voiceName}". (No custom AI persona prompt set in Manage Voices)` });
        return;
      }

      const sessionId = host.state?.activeSessionId?.get();
      try {
        await host.request('config.set', { key: 'personality', value: persona, session_id: sessionId || undefined });
        host.notify({ kind: 'success', title: 'System Persona Updated', message: `Active system prompt updated to "${voiceName}": "${persona.slice(0, 45)}..."` });
      } catch (err) {
        console.warn('Persona update failed:', err);
        host.notify({ kind: 'error', title: 'Persona Update Failed', message: err.message || 'Failed to update system persona.' });
      }
    } catch (err) {
      setActiveVoiceId(previousId);
      host.notify({ kind: 'error', title: 'Voice Update Failed', message: err.message });
    }
  };

  // ── Delete voice ─────────────────────────────
  const handleDeleteVoice = async (voiceId) => {
    setIsDeleting(true);
    try {
      await apiFetch(`/profiles/${voiceId}`, { method: 'DELETE' });
      if (voiceId === activeVoiceId) {
        setActiveVoiceId('');
        try { await saveActiveVoice(''); } catch (_) {}
      }
      setDeleteConfirmId(null);
      host.notify({ kind: 'success', title: 'Voice Deleted', message: 'Profile removed successfully.' });
      await fetchProfilesAndConfig();
    } catch (err) {
      host.notify({ kind: 'error', title: 'Delete Failed', message: err.message });
    } finally {
      setIsDeleting(false);
    }
  };

  // ── Native File Upload ───────────────────────
  const handleNativeUpload = async () => {
    if (!window.hermesDesktop?.selectPaths || !window.hermesDesktop?.readFileDataUrl) {
      host.notify({ kind: 'error', title: 'Upload Unsupported', message: 'Desktop file API is unavailable.' });
      return;
    }

    try {
      const paths = await window.hermesDesktop.selectPaths({
        properties: ['openFile'],
        filters: [{ name: 'Audio Files', extensions: ['wav', 'mp3', 'm4a', 'ogg', 'flac', 'aac', 'webm', 'opus'] }]
      });

      if (!paths || !paths.length) return;
      const filePath = paths[0];

      const nameParts = filePath.split(/[/\\]/);
      const nameWithExt = nameParts[nameParts.length - 1];
      const baseName = nameWithExt.substring(0, nameWithExt.lastIndexOf('.')) || nameWithExt;
      const ext = extensionOf(filePath);

      if (!cloneName.trim()) {
        setCloneName(baseName.replace(/[_-]/g, ' '));
      }

      const dataUrl = await window.hermesDesktop.readFileDataUrl(filePath);
      const fetchRes = await fetch(dataUrl);
      const blob = await fetchRes.blob();
      recordStartedAtRef.current = 0;
      await applyCapturedSample(blob, nameWithExt, `sample.${ext}`);
    } catch (err) {
      console.error(err);
      host.notify({ kind: 'error', title: 'File Read Failed', message: err.message });
    }
  };

  const playPlayback = () => {
    if (!audioUrl) return;
    if (isPlaying) {
      stopPlayback();
      return;
    }
    stopPlayback();
    const audio = new Audio(audioUrl);
    playbackAudioRef.current = audio;
    audio.onended = () => {
      setIsPlaying(false);
      playbackAudioRef.current = null;
    };
    audio.play().catch(err => {
      console.warn('Playback failed:', err);
      setIsPlaying(false);
      playbackAudioRef.current = null;
    });
    setIsPlaying(true);
  };

  const sampleDurationOk =
    audioDuration == null ||
    (audioDuration >= MIN_SAMPLE_SECONDS && audioDuration <= MAX_SAMPLE_SECONDS);

  // ── Clone submit ─────────────────────────────
  const handleCloneSubmit = async () => {
    if (!cloneName.trim()) {
      host.notify({ kind: 'warning', title: 'Name Required', message: 'Enter a name for the voice.' }); return;
    }
    if (recordingState === 'recording') {
      host.notify({ kind: 'warning', title: 'Still Recording', message: 'Stop the microphone recording before cloning.' }); return;
    }
    if (!audioBlob) {
      host.notify({ kind: 'warning', title: 'Audio Required', message: 'Record or upload a sample first.' }); return;
    }
    if (audioDuration != null && !sampleDurationOk) {
      host.notify({
        kind: 'warning',
        title: 'Invalid Sample Length',
        message: `Sample must be ${MIN_SAMPLE_SECONDS}–${MAX_SAMPLE_SECONDS} seconds.`
      });
      return;
    }
    const engineMeta = ENGINE_META[cloneEngine] || {};
    const savedName = cloneName.trim();
    setIsSaving(true);

    let createdProfileId = null;
    try {
      // 1. Create profile
      const profile = await apiFetch('/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: savedName, default_engine: cloneEngine })
      });
      createdProfileId = profile.id;

      // 2. Upload sample (keep real extension)
      const form = new FormData();
      form.append('file', audioBlob, uploadFileName || fileName || 'sample.wav');
      form.append('reference_text', referenceText.trim() || 'The quick brown fox jumps over the lazy dog.');
      const uploadRes = await fetch(`${BACKEND_URL}/profiles/${profile.id}/samples`, { method: 'POST', body: form });
      if (!uploadRes.ok) {
        const errData = await uploadRes.json().catch(() => ({}));
        throw new Error(errData.detail || `Audio upload returned ${uploadRes.status}`);
      }

      // Upload succeeded — profile is not orphaned
      createdProfileId = null;

      // 3. Set as active
      await handleVoiceChange(profile.id);

      // 4. Reset
      stopPlayback();
      revokeAudioUrl();
      setCloneName(''); setFileName(''); setUploadFileName('sample.wav');
      setAudioBlob(null); setAudioUrl(null); setAudioDuration(null);
      setRecordingState('idle'); setRecordElapsed(0);
      host.notify({ kind: 'success', title: 'Voice Cloned!',
        message: `"${savedName}" created using ${engineMeta.label || cloneEngine} (${engineMeta.vram || '?'} VRAM).` });

      await fetchProfilesAndConfig();
    } catch (err) {
      if (createdProfileId) {
        try {
          await apiFetch(`/profiles/${createdProfileId}`, { method: 'DELETE' });
        } catch (_) {}
      }
      host.notify({ kind: 'error', title: 'Cloning Failed', message: err.message });
    } finally {
      setIsSaving(false);
    }
  };

  const selectedEngineMeta = ENGINE_META[cloneEngine] || {};

  // ── Loading state ────────────────────────────
  if (isLoading) {
    return React.createElement('div',
      { className: 'flex flex-col items-center justify-center h-full gap-4 text-muted-foreground' },
      React.createElement('div', { className: 'animate-pulse flex flex-col items-center gap-3' }, [
        React.createElement('div', { key: 'icon', className: 'text-4xl' }, '🎙️'),
        React.createElement('p', { key: 'msg', className: 'text-sm font-medium' }, 'Connecting to Voicebox...'),
        React.createElement('div', { key: 'bar', className: 'w-32 h-1 bg-muted rounded-full overflow-hidden' },
          React.createElement('div', { className: 'h-full w-1/2 bg-primary/40 rounded-full animate-pulse' })
        )
      ])
    );
  }

  const statusDot = React.createElement('span', {
    key: 'status-dot',
    className: `inline-block w-2 h-2 rounded-full mr-2 ${
      isConnected === true ? 'bg-green-500' : isConnected === false ? 'bg-red-500' : 'bg-yellow-500'
    }`,
    title: isConnected === true ? 'Connected to Voicebox' : isConnected === false ? 'Disconnected' : 'Checking...'
  });

  const durationBadge = audioDuration != null
    ? React.createElement('span', {
        key: 'dur',
        className: `text-xs px-2 py-0.5 rounded-full font-mono ${
          sampleDurationOk
            ? 'bg-green-500/10 text-green-400 border border-green-500/30'
            : 'bg-red-500/10 text-red-400 border border-red-500/30'
        }`
      }, `${formatDuration(audioDuration)}${audioDuration < MIN_SAMPLE_SECONDS ? ' (too short)' : audioDuration > MAX_SAMPLE_SECONDS ? ' (too long)' : ''}`)
    : null;

  return React.createElement('div',
    { className: 'flex flex-col gap-6 p-8 max-w-2xl mx-auto h-full overflow-y-auto' },
    [
      // ── Header ──────────────────────────────
      React.createElement('div', { key: 'header', className: 'flex flex-col gap-1 border-b border-border pb-4' }, [
        React.createElement('div', { key: 'title-row', className: 'flex items-center gap-2' }, [
          statusDot,
          React.createElement('h1', { key: 'title', className: 'text-3xl font-bold tracking-tight' }, 'Voicebox Control'),
        ]),
        React.createElement('p', { key: 'desc', className: 'text-muted-foreground text-sm' },
          'Smart engine routing — each voice uses the right model automatically.'),
        isConnected === false && React.createElement('div', {
          key: 'reconnect',
          className: 'flex items-center gap-2 mt-2 text-xs text-red-400'
        }, [
          React.createElement('span', { key: 'msg' }, 'Backend unreachable.'),
          React.createElement(Button, {
            key: 'retry', variant: 'outline', size: 'sm',
            onClick: () => { setIsLoading(true); fetchProfilesAndConfig(); }
          }, '🔄 Retry')
        ])
      ]),

      // ── Engine legend ────────────────────────
      React.createElement('div', { key: 'legend', className: 'flex gap-3 flex-wrap' },
        Object.entries(ENGINE_META).map(([key, meta]) =>
          React.createElement('span', {
            key, className: 'inline-flex items-center gap-1.5 text-xs bg-muted/40 border border-border rounded-full px-3 py-1'
          }, [
            React.createElement('span', { key: 'badge' }, meta.badge),
            React.createElement('span', { key: 'label', className: 'font-medium' }, meta.label),
            React.createElement('span', { key: 'vram', className: 'text-muted-foreground' }, meta.vram)
          ])
        )
      ),

      // ── Active Voice Selection ───────────────
      React.createElement('div', { key: 'switcher-card', className: 'flex flex-col gap-4 bg-card border border-border rounded-lg p-6 shadow-sm' }, [
        React.createElement('h2', { key: 'h', className: 'text-xl font-semibold' }, 'Active Voice'),
        React.createElement('div', { key: 'body', className: 'flex flex-col gap-2' }, [
          React.createElement('label', { key: 'lbl', className: 'text-sm font-medium text-muted-foreground' }, 'Select Voice Profile'),
          React.createElement(Select, { key: 'sel', value: activeVoiceId, onValueChange: handleVoiceChange },
            React.createElement(SelectTrigger, { className: 'w-full h-10' },
              React.createElement(SelectValue, { placeholder: 'Select a voice...' })
            ),
            React.createElement(SelectContent, {},
              voices.map(v =>
                React.createElement(SelectItem, { key: v.id, value: v.id },
                  `${engineBadge(v)}  —  ${v.name}`
                )
              )
            )
          ),
          activeVoiceId && (() => {
            const v = voices.find(x => x.id === activeVoiceId);
            if (!v) return null;
            const { engine, meta } = getEngineMeta(v);
            const personaText = v.personality || personaMapping[v.id] || personaMapping[v.name] || personaMapping['Default'] || 'You are a helpful AI assistant.';
            return React.createElement('div', { key: 'active-info', className: 'flex flex-col gap-1 mt-1 bg-muted/30 p-3 rounded-md border border-border/50' }, [
              React.createElement('p', { key: 'v-info', className: 'text-xs text-muted-foreground' },
                `${meta.badge || '⚪'} Using ${meta.label || engine}  •  ${meta.vram || '?'} VRAM  •  ${meta.description || ''}`
              ),
              React.createElement('p', { key: 'p-info', className: 'text-sm font-semibold text-foreground mt-2' },
                `🎭 Active Persona:`
              ),
              React.createElement('p', { key: 'p-text', className: 'text-xs italic text-muted-foreground' },
                `"${personaText}"`
              )
            ]);
          })()
        ])
      ]),

      // ── Manage Voices ────────────────────────
      React.createElement('div', { key: 'manage-card', className: 'flex flex-col gap-4 bg-card border border-border rounded-lg p-6 shadow-sm' }, [
        React.createElement('h2', { key: 'h', className: 'text-xl font-semibold' }, 'Manage Voices'),
        voices.length === 0
          ? React.createElement('p', { key: 'empty', className: 'text-sm text-muted-foreground italic' }, 'No voice profiles found.')
          : React.createElement('div', { key: 'list', className: 'flex flex-col gap-2' },
              voices.map(v => {
                const { meta } = getEngineMeta(v);
                const draft = personaDrafts[v.id];
                const personaValue = draft != null
                  ? draft
                  : (v.personality || personaMapping[v.id] || personaMapping[v.name] || '');
                return React.createElement('div', {
                  key: v.id,
                  className: `flex flex-col gap-2 rounded-md px-4 py-3 border transition-colors ${v.id === activeVoiceId ? 'border-primary bg-primary/5' : 'border-border bg-muted/20 hover:bg-muted/30'}`
                }, [
                  React.createElement('div', { key: 'top-row', className: 'flex items-center justify-between' }, [
                    React.createElement('div', { key: 'info', className: 'flex items-center gap-2 min-w-0 flex-wrap' }, [
                      React.createElement('span', { key: 'name', className: 'font-medium text-sm truncate' }, v.name),
                      v.id === activeVoiceId && React.createElement('span', {
                        key: 'active',
                        className: 'text-xs bg-primary text-primary-foreground rounded-full px-2 py-0.5 shrink-0'
                      }, 'Active'),
                      React.createElement('span', {
                        key: 'eng',
                        className: 'text-xs text-muted-foreground shrink-0 font-mono'
                      }, `${meta.badge} ${meta.label}`),
                      React.createElement('span', {
                        key: 'vram',
                        className: 'text-xs text-muted-foreground/60 shrink-0'
                      }, meta.vram),
                      React.createElement('span', {
                        key: 'type',
                        className: 'text-xs text-muted-foreground/50 shrink-0'
                      }, v.voice_type === 'preset' ? '⭐ Preset' : '🎙️ Cloned')
                    ]),
                    React.createElement('div', { key: 'actions', className: 'flex items-center gap-2 shrink-0 ml-3' },
                      deleteConfirmId === v.id
                        ? [
                            React.createElement('span', { key: 'lbl', className: 'text-xs text-destructive font-medium' }, 'Delete?'),
                            React.createElement(Button, {
                              key: 'yes', variant: 'destructive', size: 'sm', disabled: isDeleting,
                              onClick: () => handleDeleteVoice(v.id)
                            }, isDeleting ? 'Deleting…' : 'Yes, Delete'),
                            React.createElement(Button, {
                              key: 'no', variant: 'outline', size: 'sm', disabled: isDeleting,
                              onClick: () => setDeleteConfirmId(null)
                            }, 'Cancel')
                          ]
                        : React.createElement(Button, {
                            key: 'del', variant: 'ghost', size: 'sm',
                            className: 'text-muted-foreground hover:text-destructive hover:bg-destructive/10',
                            onClick: () => setDeleteConfirmId(v.id)
                          }, '🗑️ Delete')
                    )
                  ]),
                  React.createElement('div', { key: 'persona-row', className: 'flex flex-col gap-1 mt-1 pt-2 border-t border-border/50' }, [
                    React.createElement('label', { className: 'text-xs font-semibold text-muted-foreground' }, '🎭 AI Persona Prompt'),
                    React.createElement(Input, {
                      value: personaValue,
                      placeholder: 'e.g. You are Vincent Price. Speak with an eerie horror host voice...',
                      onChange: (e) => schedulePersonaPersist(v, e.target.value),
                      onBlur: (e) => {
                        if (personaTimersRef.current[v.id]) {
                          clearTimeout(personaTimersRef.current[v.id]);
                          delete personaTimersRef.current[v.id];
                          persistPersona(v, e.target.value);
                        }
                      },
                      className: 'h-8 text-xs bg-background/50'
                    })
                  ])
                ]);
              })
            )
      ]),

      // ── Clone New Voice ──────────────────────
      React.createElement('div', { key: 'cloner-card', className: 'flex flex-col gap-4 bg-card border border-border rounded-lg p-6 shadow-sm' }, [
        React.createElement('h2', { key: 'h', className: 'text-xl font-semibold' }, 'Clone New Voice'),

        React.createElement('div', { key: 'engine', className: 'flex flex-col gap-2' }, [
          React.createElement('label', { key: 'lbl', className: 'text-sm font-medium text-muted-foreground' }, 'Cloning Model'),
          React.createElement(Select, { key: 'sel', value: cloneEngine, onValueChange: setCloneEngine },
            React.createElement(SelectTrigger, { className: 'w-full h-10' },
              React.createElement(SelectValue, { placeholder: 'Select cloning engine...' })
            ),
            React.createElement(SelectContent, {},
              CLONING_ENGINE_OPTIONS.map(opt =>
                React.createElement(SelectItem, { key: opt.value, value: opt.value },
                  `${opt.badge} ${opt.label}  •  ${opt.vram}  —  ${opt.note}`
                )
              )
            )
          ),
          React.createElement('div', {
            key: 'desc',
            className: `text-xs rounded-md px-3 py-2 mt-1 border ${
              cloneEngine === 'qwen' ? 'border-red-500/30 bg-red-500/5 text-red-400'
              : 'border-yellow-500/30 bg-yellow-500/5 text-yellow-400'
            }`
          },
            `${selectedEngineMeta.badge || '⚪'} ${selectedEngineMeta.description || ''}`
          )
        ]),

        React.createElement('div', { key: 'name', className: 'flex flex-col gap-2' }, [
          React.createElement('label', { key: 'lbl', className: 'text-sm font-medium text-muted-foreground' }, 'Voice Profile Name'),
          React.createElement(Input, {
            key: 'input',
            value: cloneName, placeholder: 'e.g., My Voice',
            onChange: (e) => setCloneName(e.target.value)
          })
        ]),

        React.createElement('div', { key: 'reftext', className: 'flex flex-col gap-2 bg-muted/30 border border-border/50 rounded p-4' }, [
          React.createElement('span', { key: 'lbl', className: 'text-xs font-bold text-muted-foreground uppercase tracking-wider' }, 'Read this aloud in your recording:'),
          React.createElement('p', { key: 'txt', className: 'text-lg italic font-medium leading-relaxed text-foreground' }, referenceText),
          React.createElement(Input, {
            key: 'edit',
            className: 'text-xs mt-2 text-muted-foreground bg-transparent border-none p-0 h-auto focus-visible:ring-0',
            value: referenceText, placeholder: 'Edit reference text if needed...',
            onChange: (e) => setReferenceText(e.target.value)
          })
        ]),

        React.createElement('div', { key: 'guide', className: 'flex flex-col gap-2 bg-muted/20 border border-border/50 rounded-md p-4 text-xs text-muted-foreground' }, [
          React.createElement('span', { key: 'title', className: 'font-semibold text-foreground text-sm' }, '🎙️ Voice Cloning Guidelines'),
          React.createElement('ul', { key: 'list', className: 'list-disc pl-4 flex flex-col gap-1' }, [
            React.createElement('li', { key: '1' }, 'Record in-plugin (mic) or select a file: .wav, .mp3, .m4a, .ogg, .flac, .aac, .webm, .opus — max 50 MB.'),
            React.createElement('li', { key: '2' }, `Aim for a clean ${MIN_SAMPLE_SECONDS}–${MAX_SAMPLE_SECONDS}s sample (auto-stops at ${MAX_SAMPLE_SECONDS}s).`),
            React.createElement('li', { key: '3' }, 'Ensure the reference text above matches the spoken audio exactly.')
          ])
        ]),

        React.createElement('div', { key: 'capture', className: 'flex flex-col gap-3 mt-1' }, [
          React.createElement('div', { key: 'rec-row', className: 'flex items-center gap-3 flex-wrap' }, [
            recordingState !== 'recording' && React.createElement(Button, {
              key: 'rec',
              variant: 'destructive',
              disabled: isSaving,
              onClick: startRecording
            }, recordingState === 'recorded' ? '🔄 Record Again' : '🎙️ Record Sample'),
            recordingState === 'recording' && React.createElement(Button, {
              key: 'stop',
              variant: 'destructive',
              onClick: stopRecording
            }, `⏹️ Stop (${formatDuration(recordElapsed)})`),
            React.createElement(Button, {
              key: 'upl',
              variant: 'outline',
              disabled: isSaving || recordingState === 'recording',
              onClick: handleNativeUpload
            }, '📁 Select Audio File'),
            recordingState === 'recording' && React.createElement('span', {
              key: 'live',
              className: 'text-xs text-red-400 font-mono animate-pulse'
            }, `Recording… ${formatDuration(recordElapsed)} / ${formatDuration(MAX_SAMPLE_SECONDS)}`),
          ]),
          React.createElement('div', { key: 'sample-row', className: 'flex items-center gap-3 flex-wrap' }, [
            fileName && React.createElement('span', { key: 'fn', className: 'text-xs text-muted-foreground truncate max-w-xs' }, `Sample: ${fileName}`),
            durationBadge,
            audioUrl && recordingState !== 'recording' && React.createElement(Button, {
              key: 'play', variant: 'secondary', size: 'sm', onClick: playPlayback
            }, isPlaying ? '⏸️ Pause' : '▶️ Play Sample'),
            audioUrl && recordingState === 'recorded' && React.createElement(Button, {
              key: 'clear', variant: 'ghost', size: 'sm',
              onClick: () => {
                stopPlayback();
                revokeAudioUrl();
                setAudioBlob(null);
                setAudioUrl(null);
                setAudioDuration(null);
                setFileName('');
                setUploadFileName('sample.wav');
                setRecordingState('idle');
                setRecordElapsed(0);
              }
            }, 'Clear')
          ])
        ]),

        React.createElement(Button, {
          key: 'clone-btn',
          className: 'w-full mt-4 h-11 text-base font-semibold', variant: 'default',
          disabled: isSaving || recordingState === 'recording' || !cloneName.trim() || !audioBlob || !sampleDurationOk,
          onClick: handleCloneSubmit
        }, isSaving ? 'Cloning Voice...' : `✨ Clone Voice  •  ${selectedEngineMeta.badge || ''} ${selectedEngineMeta.label || cloneEngine}`)
      ])
    ]
  );
}

export default {
  id: 'voice-switcher',
  name: 'Voicebox Integration',
  register(ctx) {
    ctx.register({ id: 'voicebox-route', area: 'routes', data: { path: '/voicebox' },
      render: () => React.createElement(VoiceboxView) });
    ctx.register({ id: 'voicebox-nav', area: 'sidebar.nav',
      data: { codicon: 'mic', label: 'Voicebox', path: '/voicebox' } });
  }
};
