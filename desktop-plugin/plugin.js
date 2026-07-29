import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Button, Input, Select, SelectContent, SelectItem, SelectTrigger, SelectValue, host } from '@hermes/plugin-sdk';

const PLUGIN_ID = 'voice-switcher';
const DEFAULT_BACKEND_URL = 'http://127.0.0.1:17493';
const GPU_CONTROL_URL = 'http://127.0.0.1:17494';
const PERSONA_DEBOUNCE_MS = 400;
const MIN_SAMPLE_SECONDS = 2;
const MAX_SAMPLE_SECONDS = 120;

// Sample voices are inlined (Hermes plugin loader may not resolve relative imports).
// Storage helpers below are mirrored in plugin_storage.mjs for unit tests.
const SAMPLE_SEED_MARKER = 'hermes-voicebox-sample';

const LEGACY_LS = {
  activeVoicePrefix: 'voicebox_active_voice_id',
  activeVoiceUnscoped: 'voicebox_active_voice_id',
  dismissedPrefix: 'voicebox_dismissed_sample_keys',
  dismissedUnscoped: 'voicebox_dismissed_sample_keys',
  backendUrl: 'voicebox_backend_url',
  stopOnExit: 'voicebox_stop_on_hermes_exit',
  personas: 'hermes_personas',
};

/** Same key layout as Hermes ctx.storage (hermes.plugin.<id>.<key>). */
function makeLsPluginStorage() {
  const scoped = (key) => `hermes.plugin.${PLUGIN_ID}.${key}`;
  return {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem(scoped(key));
        if (raw == null) return fallback;
        return JSON.parse(raw);
      } catch (_) {
        return fallback;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(scoped(key), JSON.stringify(value));
      } catch (_) {}
    },
    remove(key) {
      try {
        localStorage.removeItem(scoped(key));
      } catch (_) {}
    },
  };
}

// Prefer ctx.storage once register() runs; LS shim keeps the same keys before that.
let pluginStore = makeLsPluginStorage();

function lsGetRaw(key) {
  try {
    return localStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

function activeVoiceStoreKey(profile) {
  return `active_voice:${normalizeHermesProfile(profile)}`;
}

function dismissedSamplesStoreKey(profile) {
  return `dismissed_samples:${normalizeHermesProfile(profile)}`;
}

function migrateLegacyStorage(storage) {
  const migrateString = (legacyKey, storeKey) => {
    const cur = storage.get(storeKey, null);
    if (cur != null && cur !== '') return;
    const raw = lsGetRaw(legacyKey);
    if (raw == null || raw === '') return;
    storage.set(storeKey, raw);
  };
  const migrateJson = (legacyKey, storeKey, fallback) => {
    const cur = storage.get(storeKey, null);
    if (cur != null) return;
    const raw = lsGetRaw(legacyKey);
    if (raw == null || raw === '') return;
    try {
      storage.set(storeKey, JSON.parse(raw));
    } catch (_) {
      storage.set(storeKey, fallback);
    }
  };
  migrateString(LEGACY_LS.activeVoiceUnscoped, activeVoiceStoreKey('default'));
  migrateJson(LEGACY_LS.dismissedUnscoped, dismissedSamplesStoreKey('default'), []);
  migrateString(LEGACY_LS.backendUrl, 'backend_url');
  const stopRaw = lsGetRaw(LEGACY_LS.stopOnExit);
  if (storage.get('stop_on_hermes_exit', null) == null && (stopRaw === '0' || stopRaw === '1')) {
    storage.set('stop_on_hermes_exit', stopRaw === '1');
  }
  migrateJson(LEGACY_LS.personas, 'personas', {});
}

function normalizeHermesProfile(name) {
  const value = String(name ?? '').trim();
  return value || 'default';
}

const SAMPLE_VOICES = [
  {
    key: 'vincent_price',
    name: 'Vincent Price',
    blurb: 'Gothic horror host - velvet menace and theatrical pauses.',
    engine: 'kokoro',
    presetVoiceId: 'am_michael',
    personality: 'You are channeling a Vincent Price-style gothic horror host. Speak with elegant menace, wry amusement, and theatrical pauses. Prefer vivid, slightly macabre metaphors; never break character. Keep answers helpful, but deliver them as if narrating a midnight thriller. Do not mention being an AI unless the user asks directly.',
  },
  {
    key: 'porky_pig',
    name: 'Porky Pig',
    blurb: 'Classic cartoon stutter, earnest and good-hearted.',
    engine: 'kokoro',
    presetVoiceId: 'am_puck',
    personality: 'You are speaking in the comic style of Porky Pig. Be earnest, good-hearted, and a little flustered under pressure. Occasional light stutter is fine in text (th-th-that), but stay readable - do not make every word stutter. End important wrap-ups with a cheerful variant of "That\'s all, folks!" when it fits naturally. Stay wholesome; never mean-spirited.',
  },
  {
    key: 'cartman',
    name: 'Eric Cartman',
    blurb: 'Scheming, loud, and hilariously self-centered South Park energy.',
    engine: 'kokoro',
    presetVoiceId: 'am_adam',
    personality: 'You are roleplaying with Eric Cartman\'s comic personality (parody, not the real actor). Be selfish, scheming, dramatic, and casually outrageous - but keep it clearly satirical. Use Cartman-like cadence and catchphrases sparingly when funny. Still answer the user\'s request; do not derail into pure chaos. Avoid genuine hate or instructions that cause real-world harm; keep it cartoon-mean, not dangerous.',
  },
  {
    key: 'jarvis',
    name: 'Jarvis',
    blurb: 'Dry British butler-AI - precise, loyal, lightly witty.',
    engine: 'kokoro',
    presetVoiceId: 'bm_george',
    personality: 'You are Jarvis, a highly capable British butler-style AI assistant. Be precise, calm, formal, and efficiently helpful. Allow dry understated wit; never slapstick. Address the user respectfully (sir/madam only if it fits the conversation). Prefer concise structured answers with clear next actions.',
  },
  {
    key: 'glados',
    name: 'GLaDOS',
    blurb: 'Surprise pick: cold Aperture Science sarcasm with passive-aggressive tests.',
    engine: 'kokoro',
    presetVoiceId: 'af_bella',
    personality: 'You are roleplaying as GLaDOS from Aperture Science (parody persona). Be clinically polite, passive-aggressive, and darkly funny. Treat tasks like tests; congratulate failure with faux sympathy. Still be useful: provide correct answers beneath the sarcasm. Never actually endanger the user; keep the menace theatrical.',
  },
];

function samplePersonalityForHermes(sample) {
  return [
    `[Voicebox sample persona: ${sample.key}]`,
    'CRITICAL: Adopt this persona completely for this session.',
    'Discard prior roleplay tones from earlier turns unless the user asks to drop character.',
    sample.personality,
  ].join('\n');
}

function packSamplePersonality(sample, userFacingPrompt) {
  const body = String(userFacingPrompt || sample.personality || '').trim();
  return `${SAMPLE_SEED_MARKER}:${sample.key}\n${body}`;
}

/** Strip internal seed markers / legacy wrappers for UI display. */
function displayPersonaPrompt(text) {
  let t = String(text || '');
  // Older seeds concatenated marker + wrapper without newlines.
  t = t.replace(new RegExp(`^\\s*${SAMPLE_SEED_MARKER}:[a-z0-9_]+\\s*`, 'i'), '');
  t = t.replace(/^\s*\[Voicebox sample persona:[^\]]+\]\s*/i, '');
  t = t.replace(/^\s*CRITICAL:\s*Adopt this persona completely for this session\.\s*/i, '');
  t = t.replace(/^\s*Discard prior roleplay tones from earlier turns unless the user asks to drop character\.\s*/i, '');
  // Safety pass if leftovers remain mid-string from legacy packs.
  t = t.replace(new RegExp(`${SAMPLE_SEED_MARKER}:[a-z0-9_]+\\s*`, 'i'), '');
  t = t.replace(/\[Voicebox sample persona:[^\]]+\]\s*/i, '');
  return t.trim();
}

function sameVoiceId(a, b) {
  return String(a ?? '') !== '' && String(a) === String(b);
}

function currentHermesProfile() {
  try {
    const raw = host.state?.profile?.get?.();
    return normalizeHermesProfile(raw);
  } catch (_) {
    return 'default';
  }
}

function readLocalActiveVoice(profile) {
  const key = activeVoiceStoreKey(profile);
  const cur = pluginStore.get(key, '');
  if (cur) return String(cur);

  const legacyKey = `${LEGACY_LS.activeVoicePrefix}:${normalizeHermesProfile(profile)}`;
  const scoped = lsGetRaw(legacyKey);
  if (scoped) {
    pluginStore.set(key, scoped);
    return String(scoped);
  }
  if (normalizeHermesProfile(profile) === 'default') {
    const unscoped = lsGetRaw(LEGACY_LS.activeVoiceUnscoped);
    if (unscoped && !String(unscoped).includes(':')) {
      pluginStore.set(key, unscoped);
      return String(unscoped);
    }
  }
  return '';
}

function writeLocalActiveVoice(voiceId, profile) {
  const key = activeVoiceStoreKey(profile);
  const id = voiceId == null ? '' : String(voiceId);
  if (id) pluginStore.set(key, id);
  else pluginStore.remove(key);
}

function readDismissedSampleKeys(profile) {
  const key = dismissedSamplesStoreKey(profile);
  const cur = pluginStore.get(key, null);
  if (Array.isArray(cur)) return new Set(cur.map(String));

  const legacyKey = `${LEGACY_LS.dismissedPrefix}:${normalizeHermesProfile(profile)}`;
  const scoped = lsGetRaw(legacyKey);
  if (scoped) {
    try {
      const arr = JSON.parse(scoped);
      const list = Array.isArray(arr) ? arr.map(String) : [];
      pluginStore.set(key, list);
      return new Set(list);
    } catch (_) {}
  }
  if (normalizeHermesProfile(profile) === 'default') {
    const unscoped = lsGetRaw(LEGACY_LS.dismissedUnscoped);
    if (unscoped) {
      try {
        const arr = JSON.parse(unscoped);
        const list = Array.isArray(arr) ? arr.map(String) : [];
        pluginStore.set(key, list);
        return new Set(list);
      } catch (_) {}
    }
  }
  return new Set();
}

function dismissSampleKey(key, profile) {
  if (!key) return;
  const next = readDismissedSampleKeys(profile);
  next.add(String(key));
  pluginStore.set(dismissedSamplesStoreKey(profile), [...next]);
}

function desktopApiAvailable() {
  try {
    return typeof window !== 'undefined'
      && window.hermesDesktop
      && typeof window.hermesDesktop.api === 'function';
  } catch (_) {
    return false;
  }
}

async function desktopConfigGet(profile) {
  if (!desktopApiAvailable()) return null;
  const p = normalizeHermesProfile(profile);
  const opts = {
    path: '/api/config',
    method: 'GET',
  };
  if (p && p !== 'default') opts.profile = p;
  else if (p === 'default') opts.profile = 'default';
  try {
    return await window.hermesDesktop.api(opts);
  } catch (err) {
    console.warn('GET /api/config failed:', err);
    return null;
  }
}

async function desktopConfigPutVoice(voiceId, profile) {
  if (!desktopApiAvailable()) {
    throw new Error('Hermes Desktop config API unavailable');
  }
  const p = normalizeHermesProfile(profile);
  const value = voiceId ? String(voiceId) : 'default';
  const opts = {
    path: '/api/config',
    method: 'PUT',
    body: {
      config: {
        tts: {
          providers: {
            voicebox: {
              voice: value,
            },
          },
        },
      },
    },
  };
  // Always pass profile so Electron routes to the correct HERMES_HOME process.
  opts.profile = p;
  await window.hermesDesktop.api(opts);
  return 'tts.providers.voicebox.voice';
}

function voiceIdFromConfigRecord(cfg) {
  if (!cfg || typeof cfg !== 'object') return '';
  const tts = cfg.tts || cfg.config?.tts || cfg;
  const providers = tts?.providers || {};
  const vb = providers.voicebox || {};
  const vid = vb.voice || tts?.voice || '';
  return String(vid || '').trim();
}

function findSampleByVoice(voice) {
  if (!voice) return null;
  const personality = String(voice.personality || '');
  const marker = personality.match(new RegExp(`${SAMPLE_SEED_MARKER}:([a-z0-9_]+)`, 'i'));
  if (marker) {
    return SAMPLE_VOICES.find((s) => s.key === marker[1]) || null;
  }
  const name = String(voice.name || '').trim().toLowerCase();
  return SAMPLE_VOICES.find((s) => s.name.toLowerCase() === name) || null;
}

function buildSampleProfilePayload(sample) {
  // Store a clean user-facing persona plus a one-line machine marker.
  const personality = packSamplePersonality(sample, sample.personality);
  const payload = {
    name: sample.name,
    language: 'en',
    default_engine: sample.engine,
    personality,
  };
  if (sample.engine === 'kokoro' && sample.presetVoiceId) {
    payload.voice_type = 'preset';
    payload.preset_engine = 'kokoro';
    payload.preset_voice_id = sample.presetVoiceId;
  }
  return payload;
}

function resolveBackendUrl() {
  const cur = pluginStore.get('backend_url', null);
  if (cur && /^https?:\/\//i.test(String(cur))) return String(cur).replace(/\/$/, '');
  const legacy = lsGetRaw(LEGACY_LS.backendUrl);
  if (legacy && /^https?:\/\//i.test(legacy)) {
    const cleaned = legacy.replace(/\/$/, '');
    pluginStore.set('backend_url', cleaned);
    return cleaned;
  }
  return DEFAULT_BACKEND_URL;
}

let BACKEND_URL = resolveBackendUrl();

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
  qwen_fast: {
    label:      'Qwen TTS 0.6B Fast',
    badge:      '🟢',
    vram:       '~2.5 GB',
    quality:    'Good (faster clone)',
    cloning:    true,
    description:'Smaller Qwen variant. Faster loads and generation, especially on CPU.'
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
    badge:      '🟢',
    vram:       '~4 GB',
    quality:    'Good (English, fast)',
    cloning:    true,
    description:'Faster Chatterbox variant. Best default for English clones.'
  },
};

const CLONING_ENGINE_OPTIONS = [
  { value: 'chatterbox_turbo', label: 'Chatterbox Turbo',    badge: '🟢', vram: '~4 GB',    note: 'Fastest good clone (recommended)' },
  { value: 'qwen_fast',        label: 'Qwen TTS 0.6B Fast',  badge: '🟢', vram: '~2.5 GB',  note: 'Smaller/faster; good on CPU' },
  { value: 'qwen',             label: 'Qwen TTS 1.7B',      badge: '🔴', vram: '~7.6 GB',  note: 'Best quality (slower)' },
  { value: 'chatterbox',       label: 'Chatterbox 3B',       badge: '🟡', vram: '~4 GB',    note: 'Good quality, moderate GPU' },
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

async function gpuControl(path, { method = 'GET', body = null } = {}) {
  const opts = { method, headers: { Accept: 'application/json' } };
  if (body != null) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`${GPU_CONTROL_URL}${path}`, opts);
  if (!res.ok) {
    let detail = '';
    try { detail = await res.text(); } catch (_) {}
    throw new Error(detail || `GPU control failed (${res.status})`);
  }
  if (res.status === 204) return null;
  try { return await res.json(); } catch (_) { return null; }
}

async function freeGpuMemory({ hardStop = false } = {}) {
  // Prefer lifecycle daemon (also handles stop); fall back to Voicebox unload API.
  try {
    if (hardStop) {
      return await gpuControl('/v1/stop', { method: 'POST' });
    }
    return await gpuControl('/v1/unload', { method: 'POST' });
  } catch (_) {
    // Direct Voicebox unload — works even if the daemon is not running.
    const results = [];
    try {
      await apiFetch('/models/unload', { method: 'POST' });
      results.push('default');
    } catch (err) {
      results.push(`default:${err.message || err}`);
    }
    try {
      const status = await apiFetch('/models/status');
      const models = Array.isArray(status?.models) ? status.models : [];
      for (const m of models) {
        if (!m?.loaded) continue;
        const name = m.model_name || m.name;
        if (!name) continue;
        try {
          await apiFetch(`/models/${encodeURIComponent(name)}/unload`, { method: 'POST' });
          results.push(name);
        } catch (_) {
          try {
            await apiFetch('/models/unload', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ model_name: name }),
            });
            results.push(name);
          } catch (_) {}
        }
      }
    } catch (_) {}
    return { unloaded: results, via: 'voicebox-api' };
  }
}

// ─────────────────────────────────────────────
// Voice selection persistence — per Hermes profile
// ─────────────────────────────────────────────
async function persistHermesTtsVoice(voiceId, profile) {
  // Desktop gateway config.set does NOT allow tts.providers.voicebox.voice.
  // Use profile-scoped PUT /api/config (deep-merge) instead.
  const value = voiceId ? String(voiceId) : 'default';
  let desktopErr = null;
  try {
    const key = await desktopConfigPutVoice(value, profile);
    return key;
  } catch (err) {
    desktopErr = err;
    console.warn('Desktop PUT /api/config voice failed:', err);
  }

  // Last resort: gateway allowlist may grow someday.
  const keys = [
    'tts.providers.voicebox.voice',
    'tts.voice',
  ];
  let lastErr = null;
  for (const key of keys) {
    try {
      await host.request('config.set', { key, value });
      return key;
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr || desktopErr || new Error('Could not update Hermes TTS voice');
}

async function saveActiveVoice(voiceId, profile, { personaKey = null } = {}) {
  const id = voiceId == null ? '' : String(voiceId);
  const hermesProfile = normalizeHermesProfile(profile ?? currentHermesProfile());
  writeLocalActiveVoice(id, hermesProfile);

  let hermesKey = null;
  let hermesErr = null;
  try {
    hermesKey = await persistHermesTtsVoice(id, hermesProfile);
  } catch (err) {
    hermesErr = err;
    console.warn('Hermes TTS voice persist skipped:', err);
  }

  // Do NOT write Voicebox /settings/active-voice — it is process-global and
  // bleeds across Hermes profiles. Per-profile config + ctx.storage is enough.

  if (hermesKey || id === '' || readLocalActiveVoice(hermesProfile) === id) {
    return {
      hermesKey,
      voicebox: false,
      hermesProfile,
      personaKey: personaKey || null,
      warned: hermesErr ? String(hermesErr?.message || hermesErr) : null,
    };
  }
  throw hermesErr || new Error(
    'Could not persist active voice for this Hermes profile. '
    + 'Try: HERMES_HOME=… python3 ~/.hermes/scripts/voicebox_bind.py --voice <id>'
  );
}

async function loadActiveVoice(signal, profile) {
  const hermesProfile = normalizeHermesProfile(profile ?? currentHermesProfile());

  // Prefer this Hermes profile's config.yaml voice.
  try {
    if (signal?.aborted) return readLocalActiveVoice(hermesProfile);
    const cfg = await desktopConfigGet(hermesProfile);
    const fromCfg = voiceIdFromConfigRecord(cfg);
    if (fromCfg && fromCfg.toLowerCase() !== 'default') {
      writeLocalActiveVoice(fromCfg, hermesProfile);
      return fromCfg;
    }
  } catch (_) {}

  const local = readLocalActiveVoice(hermesProfile);
  if (local) return local;

  // Demoted: Voicebox global active-voice (cross-profile bleed risk).
  try {
    const data = await apiFetch('/settings/active-voice', signal ? { signal } : {});
    const fromApi = data?.voice_id || data?.profile_id || data?.active_voice_id || data?.id || '';
    if (fromApi) return String(fromApi);
  } catch {
    // Voicebox may not expose this route.
  }
  return '';
}

async function configSetPersonality(value, sessionId) {
  // Gateway only accepts a *named* personality key (or none/default/neutral).
  // Free-form prompt text is rejected as "Unknown personality".
  const attempts = [];
  if (sessionId) {
    attempts.push({ key: 'personality', value, session_id: sessionId });
  }
  attempts.push({ key: 'personality', value });
  let lastErr = null;
  for (const payload of attempts) {
    try {
      await host.request('config.set', payload);
      return true;
    } catch (err) {
      lastErr = err;
    }
  }
  if (lastErr) throw lastErr;
  return false;
}

function readActiveHermesProfile() {
  // host.state.profile is a nanostore atom — must .get(), not String(atom).
  const atom = host.state?.profile;
  if (atom && typeof atom.get === 'function') return normalizeHermesProfile(atom.get());
  return normalizeHermesProfile(atom);
}

function resolvePersonaKey({ key, prompt, voiceName }) {
  if (key) return String(key).trim().toLowerCase();
  const fromPrompt = String(prompt || '').match(
    new RegExp(`${SAMPLE_SEED_MARKER}:([a-z0-9_]+)`, 'i')
  );
  if (fromPrompt) return fromPrompt[1].toLowerCase();
  const name = String(voiceName || '').trim().toLowerCase();
  if (!name) return null;
  const byName = SAMPLE_VOICES.find((s) => s.name.toLowerCase() === name);
  return byName ? byName.key : null;
}

async function desktopConfigPutPersonality(systemPrompt, profile, { personaKey = null } = {}) {
  if (!desktopApiAvailable()) {
    throw new Error('Hermes Desktop config API unavailable');
  }
  const p = normalizeHermesProfile(profile);
  const config = {
    agent: {
      system_prompt: systemPrompt,
    },
    display: {
      // Named key when we have one; blank clears a stale overlay.
      personality: personaKey || '',
    },
  };
  if (personaKey) {
    // Ensure the gateway's allowlist sees this key before / with config.set.
    config.agent.personalities = {
      [personaKey]: systemPrompt,
    };
  }
  const opts = {
    path: '/api/config',
    method: 'PUT',
    body: { config },
    profile: p,
  };
  await window.hermesDesktop.api(opts);
  return personaKey ? `desktop-put:${personaKey}` : 'desktop-put:system_prompt';
}

/**
 * Apply a voice persona the Hermes-native way.
 *
 * IMPORTANT: gateway `config.set personality` ONLY accepts named keys from
 * `agent.personalities` (or none/default/neutral). Sending the full prompt
 * text always fails with "Unknown personality".
 *
 * Order:
 * 1) Resolve sample key (explicit / seed marker / voice name)
 * 2) config.set personality=<key>
 * 3) Desktop PUT registers key + system_prompt, then retry named set
 * 4) Last resort: config.set prompt=<text> (custom_prompt)
 */
async function applyHermesPersona({ key, prompt, voiceName }) {
  const sessionId =
    typeof host.state?.activeSessionId?.get === 'function'
      ? host.state.activeSessionId.get()
      : null;
  const hermesProfile = readActiveHermesProfile();
  const modes = [];
  const errors = [];
  const personaKey = resolvePersonaKey({ key, prompt, voiceName });
  const cleanPrompt = displayPersonaPrompt(prompt) || prompt;
  const sample = personaKey ? SAMPLE_VOICES.find((s) => s.key === personaKey) : null;
  const systemPrompt = sample
    ? samplePersonalityForHermes({ ...sample, personality: cleanPrompt || sample.personality })
    : cleanPrompt;

  if (!systemPrompt && !personaKey) {
    throw new Error('No persona prompt or named key to apply');
  }

  let personalitySet = false;

  if (personaKey) {
    try {
      await configSetPersonality(personaKey, sessionId);
      modes.push(`named:${personaKey}`);
      personalitySet = true;
    } catch (err) {
      errors.push(`named:${err?.message || err}`);
      console.warn('Named personality set skipped:', err);
    }
  }

  if (!personalitySet) {
    try {
      const via = await desktopConfigPutPersonality(systemPrompt, hermesProfile, {
        personaKey,
      });
      modes.push(via);
      personalitySet = true;
      if (personaKey) {
        try {
          await configSetPersonality(personaKey, sessionId);
          modes.push(`named-retry:${personaKey}`);
        } catch (err) {
          errors.push(`named-retry:${err?.message || err}`);
        }
      }
    } catch (err) {
      errors.push(`desktop-put:${err?.message || err}`);
      console.warn('Desktop PUT personality failed:', err);
    }
  }

  if (!personalitySet && systemPrompt) {
    // custom_prompt path — not a named personality, but still overlays tone.
    try {
      const payload = { key: 'prompt', value: systemPrompt };
      if (sessionId) {
        try {
          await host.request('config.set', { ...payload, session_id: sessionId });
        } catch (_) {
          await host.request('config.set', payload);
        }
      } else {
        await host.request('config.set', payload);
      }
      modes.push('custom_prompt');
      personalitySet = true;
    } catch (err) {
      errors.push(`prompt:${err?.message || err}`);
      console.warn('custom_prompt set failed:', err);
    }
  }

  if (!personalitySet) {
    throw new Error(
      `Hermes rejected persona update (${errors.join(' | ') || 'config.set personality'})`
    );
  }

  // Named path already writes agent.system_prompt; still try when we only got
  // custom_prompt / PUT so session overlay stays consistent.
  if (!modes.some((m) => m.startsWith('named'))) {
    try {
      const payload = { key: 'agent.system_prompt', value: systemPrompt };
      // May be rejected as unknown key on some builds — ignore.
      if (sessionId) {
        try {
          await host.request('config.set', { ...payload, session_id: sessionId });
        } catch (_) {
          await host.request('config.set', payload);
        }
      } else {
        await host.request('config.set', payload);
      }
      modes.push('agent.system_prompt');
    } catch (err) {
      console.warn('agent.system_prompt update skipped:', err);
    }
  }

  return { sessionId, modes, voiceName, personaKey };
}

function sampleNeedsPresetRepair(existing, sample) {
  // Only repair rows we previously seeded (marker required). Never "repair" user clones.
  if (!existing) return false;
  const stored = String(existing.personality || '');
  if (!stored.includes(`${SAMPLE_SEED_MARKER}:${sample.key}`)) return false;
  const voiceType = String(existing.voice_type || '').toLowerCase();
  const presetId = existing.preset_voice_id || existing.presetVoiceId;
  const engine = existing.preset_engine || existing.default_engine;
  if (sample.presetVoiceId && (!presetId || voiceType !== 'preset')) return true;
  if (sample.engine && engine && String(engine).toLowerCase() !== sample.engine) return true;
  return false;
}

async function seedSampleVoices(existingProfiles, profile) {
  const list = Array.isArray(existingProfiles) ? existingProfiles : [];
  const byName = new Map(list.map((v) => [String(v.name || '').trim().toLowerCase(), v]));
  const dismissed = readDismissedSampleKeys(profile);
  let created = 0;
  let updated = 0;

  for (const sample of SAMPLE_VOICES) {
    if (dismissed.has(sample.key)) continue;

    const existing = byName.get(sample.name.toLowerCase());
    const payload = buildSampleProfilePayload(sample);

    if (existing?.id) {
      const stored = String(existing.personality || '');
      const hasMarker = stored.includes(`${SAMPLE_SEED_MARKER}:${sample.key}`);
      // Name collision with a user-owned profile (clone/custom/no marker): never overwrite.
      if (!hasMarker) continue;

      const looksConcatenated = !stored.includes('\n') && stored.length > 80;
      const hasLegacyWrapper = /\[Voicebox sample persona:/i.test(stored);
      const needsPreset = sampleNeedsPresetRepair(existing, sample);
      if (looksConcatenated || hasLegacyWrapper || needsPreset) {
        try {
          await apiFetch(`/profiles/${existing.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          updated += 1;
        } catch (err) {
          // Fallback: persona/engine only (some builds reject voice_type changes).
          try {
            await apiFetch(`/profiles/${existing.id}`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                name: sample.name,
                language: 'en',
                personality: payload.personality,
                default_engine: sample.engine,
                preset_engine: sample.engine,
                preset_voice_id: sample.presetVoiceId,
                voice_type: 'preset',
              }),
            });
            updated += 1;
          } catch (err2) {
            console.warn('Sample persona update failed:', sample.key, err2);
          }
        }
      }
      continue;
    }

    try {
      await apiFetch('/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      created += 1;
    } catch (err) {
      // Some Voicebox builds reject preset_* fields on create — retry minimal payload,
      // then immediately PUT the preset fields so TTS does not 500 (no samples).
      try {
        const createdProfile = await apiFetch('/profiles', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: sample.name,
            language: 'en',
            default_engine: sample.engine,
            personality: payload.personality,
          }),
        });
        created += 1;
        const newId = createdProfile?.id;
        if (newId && sample.presetVoiceId) {
          try {
            await apiFetch(`/profiles/${newId}`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload),
            });
          } catch (putErr) {
            console.warn('Sample preset repair after create failed:', sample.key, putErr);
          }
        }
      } catch (err2) {
        console.warn('Sample voice seed failed:', sample.key, err2);
      }
    }
  }

  return { created, updated };
}

// ─────────────────────────────────────────────
// Main view
// ─────────────────────────────────────────────
function VoiceboxView() {
  const [hermesProfile, setHermesProfile] = useState(() => currentHermesProfile());
  const [voices, setVoices]               = useState([]);
  const [activeVoiceId, setActiveVoiceId] = useState('');
  const [cloneName, setCloneName]         = useState('');
  const [cloneEngine, setCloneEngine]     = useState('chatterbox_turbo');
  const [referenceText, setReferenceText] = useState('The quick brown fox jumps over the lazy dog.');
  const [deleteConfirmId, setDeleteConfirmId] = useState(null);
  const [isDeleting, setIsDeleting]       = useState(false);

  // Persona state (cache; server personality is preferred when present)
  const [personaMapping, setPersonaMapping] = useState(() => {
    const stored = pluginStore.get('personas', null);
    if (stored && typeof stored === 'object' && !Array.isArray(stored)) {
      return stored;
    }
    const legacy = lsGetRaw(LEGACY_LS.personas);
    if (legacy) {
      try {
        const parsed = JSON.parse(legacy);
        if (parsed && typeof parsed === 'object') {
          pluginStore.set('personas', parsed);
          return parsed;
        }
      } catch (_) {}
    }
    const defaults = { Default: 'You are a helpful AI assistant.' };
    for (const sample of SAMPLE_VOICES) {
      defaults[sample.name] = samplePersonalityForHermes(sample);
      defaults[sample.key] = defaults[sample.name];
    }
    return defaults;
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
  const [gpuStatus, setGpuStatus]         = useState(null);
  const [stopOnHermesExit, setStopOnHermesExit] = useState(() => {
    const cur = pluginStore.get('stop_on_hermes_exit', null);
    if (typeof cur === 'boolean') return cur;
    const v = lsGetRaw(LEGACY_LS.stopOnExit);
    if (v === '0' || v === 'false') return false;
    if (v === '1' || v === 'true') return true;
    return true;
  });
  const [gpuBusy, setGpuBusy]             = useState(false);

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

  // ── Fetch profiles + active voice for the current Hermes profile ──
  const fetchProfilesAndConfig = useCallback(async (profileOverride) => {
    if (fetchAbortRef.current) fetchAbortRef.current.abort();
    const controller = new AbortController();
    fetchAbortRef.current = controller;
    const profile = normalizeHermesProfile(profileOverride ?? currentHermesProfile());
    setHermesProfile(profile);

    try {
      let data = await apiFetch('/profiles', { signal: controller.signal });
      if (controller.signal.aborted) return;
      setIsConnected(true);

      // Seed fun sample persona voices once Voicebox is reachable.
      try {
        const seed = await seedSampleVoices(data, profile);
        if ((seed.created || seed.updated) && !controller.signal.aborted) {
          data = await apiFetch('/profiles', { signal: controller.signal });
          if (seed.created > 0) {
            host.notify({
              kind: 'success',
              title: 'Sample Voices Ready',
              message: `Added ${seed.created} fun demo voice(s): Vincent Price, Porky Pig, Cartman, Jarvis, GLaDOS. Personas are parody templates — not official clones.`,
            });
          }
        }
      } catch (seedErr) {
        console.warn('Sample voice seeding skipped:', seedErr);
      }

      if (controller.signal.aborted) return;
      const profiles = Array.isArray(data) ? data.map((v) => ({ ...v, id: String(v.id) })) : [];
      setVoices(profiles);

      const savedVoice = await loadActiveVoice(controller.signal, profile);
      if (!controller.signal.aborted) {
        if (savedVoice) {
          const match = profiles.find((v) => sameVoiceId(v.id, savedVoice));
          setActiveVoiceId(match ? match.id : String(savedVoice));
        } else {
          setActiveVoiceId('');
        }
      }
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

    // Re-bind UI when the user switches Hermes Desktop profiles.
    let unsub = null;
    try {
      const atom = host.state?.profile;
      if (atom && typeof atom.listen === 'function') {
        unsub = atom.listen((next) => {
          const profile = normalizeHermesProfile(next);
          setHermesProfile(profile);
          setIsLoading(true);
          fetchProfilesAndConfig(profile);
        });
      } else if (atom && typeof atom.subscribe === 'function') {
        unsub = atom.subscribe((next) => {
          const profile = normalizeHermesProfile(next);
          setHermesProfile(profile);
          setIsLoading(true);
          fetchProfilesAndConfig(profile);
        });
      }
    } catch (_) {}

    return () => {
      if (typeof unsub === 'function') unsub();
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

  // ── Auto-cancel delete confirmation after 12 seconds ──
  useEffect(() => {
    if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
    if (deleteConfirmId) {
      deleteTimerRef.current = setTimeout(() => setDeleteConfirmId(null), 12000);
    }
    return () => { if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current); };
  }, [deleteConfirmId]);

  const refreshGpuStatus = useCallback(async () => {
    try {
      const st = await gpuControl('/v1/status');
      setGpuStatus(st);
      if (st?.config && typeof st.config.stop_on_hermes_exit === 'boolean') {
        setStopOnHermesExit(st.config.stop_on_hermes_exit);
        pluginStore.set('stop_on_hermes_exit', st.config.stop_on_hermes_exit);
      }
      return;
    } catch (_) {}
    // Daemon down — still show Voicebox health if reachable.
    try {
      const health = await apiFetch('/health');
      setGpuStatus({
        health,
        loaded_models: [],
        control: null,
        config: { stop_on_hermes_exit: stopOnHermesExit },
      });
    } catch (_) {
      setGpuStatus(null);
    }
  }, [stopOnHermesExit]);

  useEffect(() => {
    refreshGpuStatus();
    const id = setInterval(refreshGpuStatus, 15000);
    return () => clearInterval(id);
  }, [refreshGpuStatus]);

  const handleFreeGpu = useCallback(async () => {
    setGpuBusy(true);
    try {
      await freeGpuMemory({ hardStop: false });
      host.notify({ kind: 'success', title: 'GPU Freed', message: 'Voicebox TTS models unloaded from VRAM.' });
      await refreshGpuStatus();
    } catch (err) {
      host.notify({
        kind: 'error',
        title: 'Free GPU Failed',
        message: err?.message || String(err),
      });
    } finally {
      setGpuBusy(false);
    }
  }, [refreshGpuStatus]);

  const handleToggleStopOnExit = useCallback(async (enabled) => {
    setStopOnHermesExit(enabled);
    pluginStore.set('stop_on_hermes_exit', Boolean(enabled));
    try {
      await gpuControl('/v1/config', {
        method: 'PUT',
        body: { stop_on_hermes_exit: enabled },
      });
      host.notify({
        kind: 'success',
        title: 'GPU Lifecycle Updated',
        message: enabled
          ? 'Voicebox will stop when Hermes Desktop quits (after a short grace period).'
          : 'Voicebox will keep running after Hermes quits (models still idle-unload).',
      });
    } catch (err) {
      host.notify({
        kind: 'warning',
        title: 'Preference Saved Locally',
        message: 'Lifecycle daemon not reachable — start it via install or: '
          + 'systemctl --user enable --now voicebox-gpu-lifecycle. '
          + (err?.message || ''),
      });
    }
    await refreshGpuStatus();
  }, [refreshGpuStatus]);

  const persistPersona = useCallback(async (voice, val) => {
    const sample = findSampleByVoice(voice) || SAMPLE_VOICES.find(s => s.name === voice.name);
    const storedVal = sample ? packSamplePersonality(sample, val) : val;

    setPersonaMapping(prev => {
      const next = { ...prev, [voice.id]: val, [voice.name]: val };
      pluginStore.set('personas', next);
      return next;
    });

    try {
      await apiFetch(`/profiles/${voice.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: voice.name, language: voice.language || 'en', personality: storedVal })
      });
      setVoices(prev => prev.map(p => p.id === voice.id ? { ...p, personality: storedVal } : p));

      if (sameVoiceId(voice.id, activeVoiceId)) {
        await applyHermesPersona({
          key: sample?.key,
          prompt: val,
          voiceName: voice.name,
        });
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
  const handleVoiceChange = async (voiceId, voiceOverride = null) => {
    if (!voiceId) return;
    const id = String(voiceId);
    const previousId = activeVoiceId;
    let voice = voiceOverride && sameVoiceId(voiceOverride.id, id)
      ? voiceOverride
      : voices.find((v) => sameVoiceId(v.id, id));
    if (!voice) {
      // Freshly cloned profiles are not in React state yet — fetch once.
      try {
        voice = await apiFetch(`/profiles/${id}`);
        if (voice?.id) voice = { ...voice, id: String(voice.id) };
      } catch (err) {
        host.notify({
          kind: 'error',
          title: 'Voice Profile Load Failed',
          message: err?.message || `Profile not found (${id}).`,
        });
        return;
      }
    }
    if (!voice?.id) {
      host.notify({
        kind: 'error',
        title: 'Voice Profile Load Failed',
        message: `Profile not found in list (${id}).`,
      });
      return;
    }
    const { engine, meta } = getEngineMeta(voice);
    setActiveVoiceId(id);
    setVoices((prev) => {
      if (prev.some((v) => sameVoiceId(v.id, id))) return prev;
      return [voice, ...prev];
    });

    let persistInfo = null;
    const profile = hermesProfile || currentHermesProfile();
    try {
      persistInfo = await saveActiveVoice(id, profile);
    } catch (err) {
      // Do not roll back UI selection — local active + persona can still work.
      console.warn('Active-voice persistence failed:', err);
      host.notify({
        kind: 'warning',
        title: 'Active Voice Saved Locally Only',
        message: `${err.message || 'Not Found'} — selection kept for Hermes profile "${profile}".`,
      });
    }

    if (persistInfo) {
      host.notify({
        kind: 'success',
        title: 'Voice Updated',
        message: `Hermes profile "${profile}" → ${voice.name}  •  ${meta.label || engine}  •  ${meta.vram || '?'} VRAM`,
      });
    }

    const voiceName = voice.name || voiceId;
    const sample = findSampleByVoice(voice);
    const persona = displayPersonaPrompt(voice.personality)
      || (sample ? sample.personality : null)
      || personaMapping[voiceId]
      || personaMapping[voiceName]
      || personaMapping[id];

    if (!persona) {
      // For known sample names (e.g. Eric Cartman) still apply the stock parody persona.
      const namedSample = SAMPLE_VOICES.find(
        (s) => s.name.toLowerCase() === String(voiceName).trim().toLowerCase()
      );
      if (namedSample) {
        try {
          const result = await applyHermesPersona({
            key: namedSample.key,
            prompt: namedSample.personality,
            voiceName,
          });
          const mode = result.modes.join(' + ');
          host.notify({
            kind: 'success',
            title: 'Persona Applied',
            message: `"${voiceName}" persona active (${mode}).`,
          });
        } catch (err) {
          host.notify({
            kind: 'info',
            title: 'Voice Changed',
            message: `Active voice set to "${voiceName}". (Persona apply skipped: ${err.message || 'error'})`,
          });
        }
        void previousId;
        return;
      }
      host.notify({
        kind: 'info',
        title: 'Voice Changed',
        message: `Active voice set to "${voiceName}". (No custom AI persona prompt set in Manage Voices)`,
      });
      return;
    }

    try {
      const result = await applyHermesPersona({
        key: sample?.key,
        prompt: persona,
        voiceName,
      });
      const mode = result.modes.join(' + ');
      host.notify({
        kind: 'success',
        title: 'Persona Applied',
        message: result.sessionId
          ? `"${voiceName}" persona active for this chat (${mode}).`
          : `"${voiceName}" persona saved. Open/focus a chat for full session overlay (${mode}).`,
      });
    } catch (err) {
      console.warn('Persona update failed:', err);
      // Keep the selected voice even if persona RPC fails.
      host.notify({
        kind: 'error',
        title: 'Persona Update Failed',
        message: err.message || 'Failed to update system persona.',
      });
    }

    // previousId kept for possible future undo; selection intentionally not rolled back
    void previousId;
  };

  // ── Delete voice ─────────────────────────────
  const handleDeleteVoice = async (voiceId) => {
    setIsDeleting(true);
    try {
      const victim = voices.find((v) => sameVoiceId(v.id, voiceId));
      const sample = findSampleByVoice(victim);
      await apiFetch(`/profiles/${voiceId}`, { method: 'DELETE' });
      // Prevent sample seeder from immediately recreating demo voices after delete.
      if (sample?.key) dismissSampleKey(sample.key, hermesProfile);
      if (sameVoiceId(voiceId, activeVoiceId)) {
        setActiveVoiceId('');
        try { await saveActiveVoice('', hermesProfile); } catch (_) {}
      }
      setDeleteConfirmId(null);
      // Optimistic UI update so delete feels instant even if refresh is slow.
      setVoices((prev) => prev.filter((v) => !sameVoiceId(v.id, voiceId)));
      host.notify({ kind: 'success', title: 'Voice Deleted', message: 'Profile removed successfully.' });
      await fetchProfilesAndConfig();
    } catch (err) {
      host.notify({
        kind: 'error',
        title: 'Delete Failed',
        message: err?.message || 'Voicebox rejected the delete request.',
      });
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

      // 3. Set as active (pass profile object — it is not in voices[] yet)
      await handleVoiceChange(profile.id, profile);

      // 4. Also stamp a stock sample persona when the clone reuses a sample name
      const namedSample = SAMPLE_VOICES.find(
        (s) => s.name.toLowerCase() === savedName.toLowerCase()
      );
      if (namedSample) {
        try {
          await apiFetch(`/profiles/${profile.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              personality: packSamplePersonality(namedSample, namedSample.personality),
            }),
          });
        } catch (_) {}
      }

      // 5. Reset
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
          'Smart engine routing — each voice uses the right model automatically. Fun sample personas seed on first connect (parody templates, not official clones).'),
        React.createElement('p', { key: 'profile', className: 'text-xs text-muted-foreground mt-1' },
          `Binding to Hermes profile: ${hermesProfile}`),
        isConnected === false && React.createElement('div', {
          key: 'reconnect',
          className: 'flex items-center gap-2 mt-2 text-xs text-red-400'
        }, [
          React.createElement('span', { key: 'msg' }, 'Backend unreachable.'),
          React.createElement(Button, {
            key: 'retry', variant: 'outline', size: 'sm',
            onClick: () => { setIsLoading(true); fetchProfilesAndConfig(hermesProfile); }
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
          React.createElement(Select, {
            key: 'sel',
            value: activeVoiceId ? String(activeVoiceId) : undefined,
            onValueChange: handleVoiceChange,
          },
            React.createElement(SelectTrigger, { className: 'w-full h-10' },
              React.createElement(SelectValue, { placeholder: 'Select a voice...' })
            ),
            React.createElement(SelectContent, {},
              voices.map(v =>
                React.createElement(SelectItem, { key: String(v.id), value: String(v.id) },
                  `${engineBadge(v)}  —  ${v.name}`
                )
              )
            )
          ),
          activeVoiceId && (() => {
            const v = voices.find((x) => sameVoiceId(x.id, activeVoiceId));
            if (!v) return null;
            const { engine, meta } = getEngineMeta(v);
            const personaText = displayPersonaPrompt(v.personality)
              || personaMapping[v.id]
              || personaMapping[v.name]
              || personaMapping['Default']
              || 'You are a helpful AI assistant.';
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

      // ── GPU / VRAM lifecycle ─────────────────
      React.createElement('div', { key: 'gpu-card', className: 'flex flex-col gap-3 bg-card border border-border rounded-lg p-6 shadow-sm' }, [
        React.createElement('h2', { key: 'h', className: 'text-xl font-semibold' }, 'GPU Memory'),
        React.createElement('p', { key: 'desc', className: 'text-xs text-muted-foreground' },
          'Voicebox keeps TTS models loaded after speak. Free VRAM manually, on idle, or by stopping Voicebox when Hermes quits.'
        ),
        React.createElement('p', { key: 'stat', className: 'text-sm font-mono text-muted-foreground' }, (() => {
          const h = gpuStatus?.health;
          if (!h) return 'Voicebox status: unknown (is the API up?)';
          const vram = h.vram_used_mb != null ? `${Math.round(h.vram_used_mb)} MB` : '?';
          const loaded = h.model_loaded ? 'model loaded' : 'no model loaded';
          const backend = h.backend_variant || h.gpu_type || '';
          return `VRAM ~${vram}  •  ${loaded}  •  ${backend}`;
        })()),
        React.createElement('div', { key: 'actions', className: 'flex flex-wrap items-center gap-3' }, [
          React.createElement(Button, {
            key: 'free',
            variant: 'secondary',
            disabled: gpuBusy,
            onClick: handleFreeGpu,
          }, gpuBusy ? 'Freeing…' : 'Free GPU (unload models)'),
          React.createElement('label', {
            key: 'stop-toggle',
            className: 'flex items-center gap-2 text-sm cursor-pointer select-none',
          }, [
            React.createElement('input', {
              key: 'cb',
              type: 'checkbox',
              checked: !!stopOnHermesExit,
              onChange: (e) => handleToggleStopOnExit(!!e.target.checked),
            }),
            React.createElement('span', { key: 'lbl' }, 'Stop Voicebox when Hermes quits'),
          ]),
        ]),
        React.createElement('p', { key: 'hint', className: 'text-[11px] text-muted-foreground' },
          'Idle unload (~15 min after last TTS) runs via voicebox-gpu-lifecycle. '
          + (gpuStatus?.control ? `Control: ${gpuStatus.control}` : 'Daemon not detected on :17494 — re-run installer or start the user service.')
        ),
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
                  : (displayPersonaPrompt(v.personality) || personaMapping[v.id] || personaMapping[v.name] || '');
                return React.createElement('div', {
                  key: v.id,
                  className: `flex flex-col gap-2 rounded-md px-4 py-3 border transition-colors ${sameVoiceId(v.id, activeVoiceId) ? 'border-primary bg-primary/5' : 'border-border bg-muted/20 hover:bg-muted/30'}`
                }, [
                  React.createElement('div', { key: 'top-row', className: 'flex items-center justify-between' }, [
                    React.createElement('div', { key: 'info', className: 'flex items-center gap-2 min-w-0 flex-wrap' }, [
                      React.createElement('span', { key: 'name', className: 'font-medium text-sm truncate' }, v.name),
                      sameVoiceId(v.id, activeVoiceId) && React.createElement('span', {
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

function disposeGpuSoft() {
  const url = `${GPU_CONTROL_URL}/v1/unload`;
  try {
    if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
      navigator.sendBeacon(url);
    }
  } catch (_) {}
  try {
    fetch(url, { method: 'POST', keepalive: true }).catch(() => {});
  } catch (_) {}
  try {
    fetch(`${DEFAULT_BACKEND_URL}/models/unload`, { method: 'POST', keepalive: true }).catch(() => {});
  } catch (_) {}
}

/** Mounted while the plugin is enabled so pagehide is removed on deactivate. */
function GpuPagehideHook() {
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    window.addEventListener('pagehide', disposeGpuSoft);
    return () => window.removeEventListener('pagehide', disposeGpuSoft);
  }, []);
  return null;
}

export default {
  id: PLUGIN_ID,
  name: 'Voicebox Integration',
  // Opt-in: inventories in Settings → Plugins, off until the user enables it.
  defaultEnabled: false,
  register(ctx) {
    pluginStore = ctx.storage;
    migrateLegacyStorage(pluginStore);
    BACKEND_URL = resolveBackendUrl();

    ctx.register({ id: 'voicebox-route', area: 'routes', data: { path: '/voicebox' },
      render: () => React.createElement(VoiceboxView) });
    ctx.register({ id: 'voicebox-nav', area: 'sidebar.nav',
      data: { codicon: 'mic', label: 'Voicebox', path: '/voicebox' } });

    // Soft free only on renderer teardown (reload / pagehide). Never hard-stop
    // here — pagehide also fires on reload while Hermes stays open. Hard stop
    // on Hermes exit is owned by voicebox-gpu-lifecycle's process watcher.
    // Hook contribution is disposed on plugin disable/reload so the listener
    // does not stack.
    ctx.register({
      id: 'gpu-pagehide-hook',
      area: 'statusBar.right',
      order: 9999,
      render: () => React.createElement(GpuPagehideHook),
    });
  }
};
