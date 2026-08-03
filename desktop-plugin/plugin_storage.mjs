/**
 * Plugin-scoped storage helpers for Voicebox desktop plugin.
 *
 * Mirrored inline in plugin.js (Hermes disk loader cannot resolve relative
 * imports from the blob-evaluated plugin.js). Keep both in sync.
 *
 * ctx.storage JSON-encodes values under hermes.plugin.voice-switcher.<key>.
 */

export const LEGACY = {
  activeVoicePrefix: 'voicebox_active_voice_id',
  activeVoiceUnscoped: 'voicebox_active_voice_id',
  dismissedPrefix: 'voicebox_dismissed_sample_keys',
  dismissedUnscoped: 'voicebox_dismissed_sample_keys',
  backendUrl: 'voicebox_backend_url',
  stopOnExit: 'voicebox_stop_on_hermes_exit',
  personas: 'hermes_personas',
};

export function normalizeHermesProfile(name) {
  const value = String(name ?? '').trim();
  return value || 'default';
}

export function activeVoiceKey(profile) {
  return `active_voice:${normalizeHermesProfile(profile)}`;
}

export function dismissedSamplesKey(profile) {
  return `dismissed_samples:${normalizeHermesProfile(profile)}`;
}

function lsGet(rawGet, key) {
  try {
    return rawGet(key);
  } catch {
    return null;
  }
}

/**
 * One-time migrate bare localStorage keys into ctx.storage.
 * Does not overwrite keys that already exist in plugin storage.
 *
 * @param {{ get: Function, set: Function, remove?: Function }} storage ctx.storage
 * @param {(key: string) => string|null} rawGet localStorage.getItem bound
 */
export function migrateLegacyStorage(storage, rawGet = (k) => localStorage.getItem(k)) {
  const migrateString = (legacyKey, storeKey) => {
    const cur = storage.get(storeKey, null);
    if (cur != null && cur !== '') return;
    const raw = lsGet(rawGet, legacyKey);
    if (raw == null || raw === '') return;
    storage.set(storeKey, raw);
  };

  const migrateJson = (legacyKey, storeKey, fallback) => {
    const cur = storage.get(storeKey, null);
    if (cur != null) return;
    const raw = lsGet(rawGet, legacyKey);
    if (raw == null || raw === '') return;
    try {
      storage.set(storeKey, JSON.parse(raw));
    } catch {
      storage.set(storeKey, fallback);
    }
  };

  // Unscoped active voice → default profile
  migrateString(LEGACY.activeVoiceUnscoped, activeVoiceKey('default'));

  // Scan is not possible without knowing profiles; migrate common pattern by
  // reading unscoped + letting callers migrate profile-scoped on first read.
  migrateJson(LEGACY.dismissedUnscoped, dismissedSamplesKey('default'), []);
  migrateString(LEGACY.backendUrl, 'backend_url');

  const stopRaw = lsGet(rawGet, LEGACY.stopOnExit);
  if (storage.get('stop_on_hermes_exit', null) == null && (stopRaw === '0' || stopRaw === '1')) {
    storage.set('stop_on_hermes_exit', stopRaw === '1');
  }

  migrateJson(LEGACY.personas, 'personas', {});
}

/**
 * Read active voice, migrating profile-scoped legacy key on demand.
 */
export function readActiveVoice(storage, profile, rawGet = (k) => localStorage.getItem(k)) {
  const key = activeVoiceKey(profile);
  const cur = storage.get(key, '');
  if (cur) return String(cur);

  const legacyKey = `${LEGACY.activeVoicePrefix}:${normalizeHermesProfile(profile)}`;
  const scoped = lsGet(rawGet, legacyKey);
  if (scoped) {
    storage.set(key, scoped);
    return String(scoped);
  }

  if (normalizeHermesProfile(profile) === 'default') {
    const unscoped = lsGet(rawGet, LEGACY.activeVoiceUnscoped);
    if (unscoped && !String(unscoped).includes(':')) {
      storage.set(key, unscoped);
      return String(unscoped);
    }
  }
  return '';
}

export function writeActiveVoice(storage, voiceId, profile) {
  const key = activeVoiceKey(profile);
  const id = voiceId == null ? '' : String(voiceId);
  if (id) storage.set(key, id);
  else storage.remove(key);
}

export function readDismissedSamples(storage, profile, rawGet = (k) => localStorage.getItem(k)) {
  const key = dismissedSamplesKey(profile);
  const cur = storage.get(key, null);
  if (Array.isArray(cur)) return new Set(cur.map(String));

  const legacyKey = `${LEGACY.dismissedPrefix}:${normalizeHermesProfile(profile)}`;
  const scoped = lsGet(rawGet, legacyKey);
  if (scoped) {
    try {
      const arr = JSON.parse(scoped);
      const list = Array.isArray(arr) ? arr.map(String) : [];
      storage.set(key, list);
      return new Set(list);
    } catch {
      /* fall through */
    }
  }

  if (normalizeHermesProfile(profile) === 'default') {
    const unscoped = lsGet(rawGet, LEGACY.dismissedUnscoped);
    if (unscoped) {
      try {
        const arr = JSON.parse(unscoped);
        const list = Array.isArray(arr) ? arr.map(String) : [];
        storage.set(key, list);
        return new Set(list);
      } catch {
        /* fall through */
      }
    }
  }
  return new Set();
}

export function dismissSample(storage, sampleKey, profile, rawGet) {
  if (!sampleKey) return;
  const next = readDismissedSamples(storage, profile, rawGet);
  next.add(String(sampleKey));
  storage.set(dismissedSamplesKey(profile), [...next]);
}

export function readBackendUrl(storage, fallback, rawGet = (k) => localStorage.getItem(k)) {
  const cur = storage.get('backend_url', null);
  if (cur && /^https?:\/\//i.test(String(cur))) return String(cur).replace(/\/$/, '');
  const legacy = lsGet(rawGet, LEGACY.backendUrl);
  if (legacy && /^https?:\/\//i.test(legacy)) {
    const cleaned = legacy.replace(/\/$/, '');
    storage.set('backend_url', cleaned);
    return cleaned;
  }
  return fallback;
}

export function readStopOnExit(storage, rawGet = (k) => localStorage.getItem(k)) {
  const cur = storage.get('stop_on_hermes_exit', null);
  if (typeof cur === 'boolean') return cur;
  const legacy = lsGet(rawGet, LEGACY.stopOnExit);
  if (legacy === '0') return false;
  if (legacy === '1') return true;
  return true;
}

export function writeStopOnExit(storage, enabled) {
  storage.set('stop_on_hermes_exit', Boolean(enabled));
}

export function readPersonas(storage, rawGet = (k) => localStorage.getItem(k)) {
  const cur = storage.get('personas', null);
  if (cur && typeof cur === 'object' && !Array.isArray(cur)) return cur;
  const legacy = lsGet(rawGet, LEGACY.personas);
  if (legacy) {
    try {
      const parsed = JSON.parse(legacy);
      if (parsed && typeof parsed === 'object') {
        storage.set('personas', parsed);
        return parsed;
      }
    } catch {
      /* fall through */
    }
  }
  return {};
}

export function writePersonas(storage, personas) {
  storage.set('personas', personas && typeof personas === 'object' ? personas : {});
}

/** Memory-backed PluginStorage for tests. */
export function memoryStorage() {
  const map = new Map();
  return {
    get(key, fallback) {
      return map.has(key) ? map.get(key) : fallback;
    },
    set(key, value) {
      map.set(key, value);
    },
    remove(key) {
      map.delete(key);
    },
    _map: map,
  };
}
