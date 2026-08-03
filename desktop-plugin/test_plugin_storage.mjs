import assert from 'node:assert/strict';
import {
  migrateLegacyStorage,
  memoryStorage,
  readActiveVoice,
  writeActiveVoice,
  readDismissedSamples,
  dismissSample,
  readBackendUrl,
  readStopOnExit,
  writeStopOnExit,
  readPersonas,
  writePersonas,
  activeVoiceKey,
  dismissedSamplesKey,
} from './plugin_storage.mjs';

function ls(map) {
  return (key) => (map.has(key) ? map.get(key) : null);
}

{
  const storage = memoryStorage();
  const legacy = new Map([
    ['voicebox_active_voice_id', 'voice-default'],
    ['voicebox_dismissed_sample_keys', JSON.stringify(['cartman'])],
    ['voicebox_backend_url', 'http://127.0.0.1:9999/'],
    ['voicebox_stop_on_hermes_exit', '0'],
    ['hermes_personas', JSON.stringify({ jarvis: 'at your service' })],
  ]);
  migrateLegacyStorage(storage, ls(legacy));
  assert.equal(storage.get(activeVoiceKey('default'), ''), 'voice-default');
  assert.deepEqual(storage.get(dismissedSamplesKey('default'), null), ['cartman']);
  assert.equal(storage.get('backend_url', null), 'http://127.0.0.1:9999/');
  assert.equal(storage.get('stop_on_hermes_exit', null), false);
  assert.deepEqual(storage.get('personas', null), { jarvis: 'at your service' });
}

{
  const storage = memoryStorage();
  storage.set(activeVoiceKey('default'), 'keep-me');
  const legacy = new Map([['voicebox_active_voice_id', 'ignore-me']]);
  migrateLegacyStorage(storage, ls(legacy));
  assert.equal(storage.get(activeVoiceKey('default'), ''), 'keep-me');
}

{
  const storage = memoryStorage();
  const legacy = new Map([
    ['voicebox_active_voice_id:cartman', 'voice-cartman'],
  ]);
  assert.equal(readActiveVoice(storage, 'cartman', ls(legacy)), 'voice-cartman');
  assert.equal(storage.get(activeVoiceKey('cartman'), ''), 'voice-cartman');
  writeActiveVoice(storage, 'voice-2', 'cartman');
  assert.equal(readActiveVoice(storage, 'cartman', ls(new Map())), 'voice-2');
  writeActiveVoice(storage, '', 'cartman');
  assert.equal(readActiveVoice(storage, 'cartman', ls(new Map())), '');
}

{
  const storage = memoryStorage();
  const legacy = new Map([
    ['voicebox_dismissed_sample_keys:work', JSON.stringify(['glados'])],
  ]);
  const set = readDismissedSamples(storage, 'work', ls(legacy));
  assert.ok(set.has('glados'));
  dismissSample(storage, 'jarvis', 'work', ls(new Map()));
  assert.ok(readDismissedSamples(storage, 'work', ls(new Map())).has('jarvis'));
}

{
  const storage = memoryStorage();
  assert.equal(
    readBackendUrl(storage, 'http://127.0.0.1:17493', ls(new Map([['voicebox_backend_url', 'http://example.test']]))),
    'http://example.test'
  );
  assert.equal(readStopOnExit(storage, ls(new Map([['voicebox_stop_on_hermes_exit', '0']]))), false);
  writeStopOnExit(storage, true);
  assert.equal(readStopOnExit(storage, ls(new Map())), true);
  writePersonas(storage, { a: 'b' });
  assert.deepEqual(readPersonas(storage, ls(new Map())), { a: 'b' });
}

console.log('ok - plugin_storage tests passed');
