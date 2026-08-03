/**
 * Fun demo voice packs for Hermes Voicebox.
 *
 * These are PERSONA + TTS STYLE templates — not official celebrity/character
 * voice clones and not copyrighted audio. Kokoro preset IDs give an immediate
 * speakable demo; users can later re-clone with their own reference audio on
 * Qwen/Chatterbox for a closer sound match.
 */

export const SAMPLE_SEED_MARKER = 'hermes-voicebox-sample';

/** @type {Array<{
 *  key: string,
 *  name: string,
 *  blurb: string,
 *  engine: string,
 *  presetVoiceId?: string,
 *  personality: string
 * }>} */
export const SAMPLE_VOICES = [];

export function samplePersonalityForHermes(sample) {
  // Hermes /personality overlay — strong, leakage-resistant directive.
  return [
    `[Voicebox sample persona: ${sample.key}]`,
    `CRITICAL: Adopt this persona completely for this session.`,
    `Discard prior roleplay tones from earlier turns unless the user asks to drop character.`,
    sample.personality,
  ].join('\n');
}

export function findSampleByVoice(voice) {
  if (!voice) return null;
  const personality = String(voice.personality || '');
  const marker = personality.match(new RegExp(`${SAMPLE_SEED_MARKER}:([a-z0-9_]+)`));
  if (marker) {
    return SAMPLE_VOICES.find((s) => s.key === marker[1]) || null;
  }
  const name = String(voice.name || '').trim().toLowerCase();
  return SAMPLE_VOICES.find((s) => s.name.toLowerCase() === name) || null;
}

export function buildSampleProfilePayload(sample) {
  const personality = [
    `${SAMPLE_SEED_MARKER}:${sample.key}`,
    samplePersonalityForHermes(sample),
  ].join('\n');

  const payload = {
    name: sample.name,
    language: 'en',
    default_engine: sample.engine,
    personality,
  };
  // Best-effort Kokoro preset binding when the API accepts these fields.
  if (sample.engine === 'kokoro' && sample.presetVoiceId) {
    payload.voice_type = 'preset';
    payload.preset_engine = 'kokoro';
    payload.preset_voice_id = sample.presetVoiceId;
  }
  return payload;
}
