import React, { useState, useEffect, useRef } from 'react';
import { Button, Input, Select, SelectContent, SelectItem, SelectTrigger, SelectValue, host } from '@hermes/plugin-sdk';

const BACKEND_URL = 'http://localhost:17493';

// ─────────────────────────────────────────────
// Engine metadata — resource cost & capabilities
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

// Cloning engine choices shown in the UI (only downloaded ones shown)
const CLONING_ENGINE_OPTIONS = [
  { value: 'qwen',             label: 'Qwen TTS 1.7B',      badge: '🔴', vram: '~7.6 GB',  note: 'Best quality' },
  { value: 'chatterbox',       label: 'Chatterbox 3B',       badge: '🟡', vram: '~4 GB',    note: 'Good quality, moderate GPU' },
  { value: 'chatterbox_turbo', label: 'Chatterbox Turbo',    badge: '🟡', vram: '~4 GB',    note: 'Fast English cloning' },
];

function engineBadge(voice) {
  const eng = voice.preset_engine || voice.default_engine || (voice.voice_type === 'preset' ? 'kokoro' : 'qwen');
  const meta = ENGINE_META[eng] || { badge: '⚪', label: eng };
  return `${meta.badge} ${meta.label}`;
}

function engineVram(voice) {
  const eng = voice.preset_engine || voice.default_engine || (voice.voice_type === 'preset' ? 'kokoro' : 'qwen');
  return ENGINE_META[eng]?.vram || '';
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

  // Recording state
  const [recordingState, setRecordingState] = useState('idle');
  const [audioUrl, setAudioUrl]           = useState(null);
  const [audioBlob, setAudioBlob]         = useState(null);
  const [isSaving, setIsSaving]           = useState(false);
  const [isPlaying, setIsPlaying]         = useState(false);

  const mediaRecorderRef = useRef(null);
  const audioChunksRef   = useRef([]);
  const playbackAudioRef = useRef(null);

  // ── Fetch profiles + active config ──────────
  const fetchProfilesAndConfig = async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/profiles`);
      if (!res.ok) throw new Error('Failed to fetch profiles');
      setVoices(await res.json());

      const cfg = await window.hermesDesktop.api({ path: '/api/config', method: 'GET' });
      setActiveVoiceId(cfg?.tts?.providers?.voicebox?.voice || '');
    } catch (err) {
      console.error(err);
      host.notify({ kind: 'error', title: 'Voicebox Connection Failed',
        message: 'Could not connect to Voicebox backend. Is it running?' });
    }
  };

  useEffect(() => {
    fetchProfilesAndConfig();
    return () => { if (playbackAudioRef.current) playbackAudioRef.current.pause(); };
  }, []);

  // ── Active voice selection ───────────────────
  const handleVoiceChange = async (voiceId) => {
    if (!voiceId) return;
    const voice = voices.find(v => v.id === voiceId);
    const engine = voice?.preset_engine || voice?.default_engine || (voice?.voice_type === 'preset' ? 'kokoro' : 'qwen');
    const meta = ENGINE_META[engine] || {};
    try {
      setActiveVoiceId(voiceId);
      await window.hermesDesktop.api({
        path: '/api/config', method: 'PUT',
        body: {
          config: {
            tts: {
              provider: 'voicebox',
              providers: {
                voicebox: {
                  type: 'command',
                  command: 'python3 /home/chuck/.hermes/scripts/voicebox_tts.py --text-file {input_path} --out {output_path} --voice {voice}',
                  voice: voiceId,
                  output_format: 'wav'
                }
              }
            }
          }
        }
      });
      host.notify({ kind: 'success', title: 'Voice Updated',
        message: `Active voice: ${voice?.name || voiceId}  •  ${meta.label || engine}  •  VRAM ${meta.vram || '?'}` });
    } catch (err) {
      host.notify({ kind: 'error', title: 'Config Update Failed', message: err.message });
    }
  };

  // ── Delete voice ─────────────────────────────
  const handleDeleteVoice = async (voiceId) => {
    setIsDeleting(true);
    try {
      const res = await fetch(`${BACKEND_URL}/profiles/${voiceId}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
      if (voiceId === activeVoiceId) {
        setActiveVoiceId('');
        try {
          await window.hermesDesktop.api({ path: '/api/config', method: 'PUT',
            body: { config: { tts: { provider: 'none', providers: { voicebox: { voice: '' } } } } } });
        } catch (_) {}
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

  // ── Recording ────────────────────────────────
  const startRecording = async () => {
    audioChunksRef.current = [];
    try {
      if (window.hermesDesktop?.requestMicrophoneAccess) {
        const permitted = await window.hermesDesktop.requestMicrophoneAccess();
        if (permitted === false) {
          host.notify({ kind: 'error', title: 'Mic Access Denied', message: 'Microphone access was denied.' });
          return;
        }
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true }
      });

      const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus', 'audio/ogg', 'audio/wav'].find(
        type => MediaRecorder.isTypeSupported(type)
      ) ?? '';

      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data); };
      recorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: mimeType || 'audio/webm' });
        setAudioBlob(blob);
        setAudioUrl(URL.createObjectURL(blob));
        setRecordingState('recorded');
        stream.getTracks().forEach(t => t.stop());
      };
      recorder.start();
      setRecordingState('recording');
    } catch (err) {
      console.error(err);
      host.notify({ kind: 'error', title: 'Mic Access Denied',
        message: 'Could not access microphone: ' + err.message });
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && recordingState === 'recording')
      mediaRecorderRef.current.stop();
  };

  const handleFileUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    if (!cloneName.trim()) {
      const base = file.name.substring(0, file.name.lastIndexOf('.')) || file.name;
      setCloneName(base.replace(/[_-]/g, ' '));
    }
    setAudioBlob(file);
    setAudioUrl(URL.createObjectURL(file));
    setRecordingState('recorded');
  };

  const playPlayback = () => {
    if (!audioUrl) return;
    if (isPlaying) { playbackAudioRef.current.pause(); setIsPlaying(false); }
    else {
      const audio = new Audio(audioUrl);
      playbackAudioRef.current = audio;
      audio.onended = () => setIsPlaying(false);
      audio.play();
      setIsPlaying(true);
    }
  };

  // ── Clone submit ─────────────────────────────
  const handleCloneSubmit = async () => {
    if (!cloneName.trim()) {
      host.notify({ kind: 'warning', title: 'Name Required', message: 'Enter a name for the voice.' }); return;
    }
    if (!audioBlob) {
      host.notify({ kind: 'warning', title: 'Audio Required', message: 'Record or upload a sample first.' }); return;
    }
    const engineMeta = ENGINE_META[cloneEngine] || {};
    setIsSaving(true);
    try {
      // 1. Create profile — set default_engine at creation time
      const createRes = await fetch(`${BACKEND_URL}/profiles`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: cloneName.trim(), default_engine: cloneEngine })
      });
      if (!createRes.ok) throw new Error('Failed to create voice profile');
      const profile = await createRes.json();

      // 2. Upload sample
      const form = new FormData();
      form.append('file', audioBlob, 'sample.webm');
      form.append('reference_text', referenceText.trim() || 'The quick brown fox jumps over the lazy dog.');
      const uploadRes = await fetch(`${BACKEND_URL}/profiles/${profile.id}/samples`, { method: 'POST', body: form });
      if (!uploadRes.ok) throw new Error('Failed to upload audio sample');

      // 3. Set as active
      await handleVoiceChange(profile.id);

      // 4. Reset
      setCloneName(''); setAudioBlob(null); setAudioUrl(null); setRecordingState('idle');
      host.notify({ kind: 'success', title: 'Voice Cloned!',
        message: `"${cloneName}" created using ${engineMeta.label || cloneEngine} (${engineMeta.vram || '?'} VRAM).` });
      await fetchProfilesAndConfig();
    } catch (err) {
      host.notify({ kind: 'error', title: 'Cloning Failed', message: err.message });
    } finally {
      setIsSaving(false);
    }
  };

  const selectedEngineMeta = ENGINE_META[cloneEngine] || {};

  // ─────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────
  return React.createElement('div',
    { className: 'flex flex-col gap-6 p-8 max-w-2xl mx-auto h-full overflow-y-auto' },
    [
      // ── Header ──────────────────────────────
      React.createElement('div', { key: 'header', className: 'flex flex-col gap-1 border-b border-border pb-4' }, [
        React.createElement('h1', { className: 'text-3xl font-bold tracking-tight' }, 'Voicebox Control'),
        React.createElement('p', { className: 'text-muted-foreground text-sm' },
          'Smart engine routing — each voice uses the right model automatically.')
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
        React.createElement('h2', { className: 'text-xl font-semibold' }, 'Active Voice'),
        React.createElement('div', { className: 'flex flex-col gap-2' }, [
          React.createElement('label', { className: 'text-sm font-medium text-muted-foreground' }, 'Select Voice Profile'),
          React.createElement(Select, { value: activeVoiceId, onValueChange: handleVoiceChange },
            React.createElement(SelectTrigger, { className: 'w-full h-10' }, [
              React.createElement(SelectValue, { placeholder: 'Select a voice...' })
            ]),
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
            const eng = v.preset_engine || v.default_engine || (v.voice_type === 'preset' ? 'kokoro' : 'qwen');
            const meta = ENGINE_META[eng] || {};
            return React.createElement('p', { key: 'active-info', className: 'text-xs text-muted-foreground mt-1' },
              `${meta.badge || '⚪'} Using ${meta.label || eng}  •  ${meta.vram || '?'} VRAM  •  ${meta.description || ''}`
            );
          })()
        ])
      ]),

      // ── Manage Voices ────────────────────────
      React.createElement('div', { key: 'manage-card', className: 'flex flex-col gap-4 bg-card border border-border rounded-lg p-6 shadow-sm' }, [
        React.createElement('h2', { className: 'text-xl font-semibold' }, 'Manage Voices'),
        voices.length === 0
          ? React.createElement('p', { className: 'text-sm text-muted-foreground italic' }, 'No voice profiles found.')
          : React.createElement('div', { className: 'flex flex-col gap-2' },
              voices.map(v => {
                const eng  = v.preset_engine || v.default_engine || (v.voice_type === 'preset' ? 'kokoro' : 'qwen');
                const meta = ENGINE_META[eng] || { badge: '⚪', label: eng, vram: '' };
                return React.createElement('div', {
                  key: v.id,
                  className: `flex items-center justify-between rounded-md px-4 py-3 border ${v.id === activeVoiceId ? 'border-primary bg-primary/5' : 'border-border bg-muted/20'}`
                }, [
                  // Left: name + badges
                  React.createElement('div', { key: 'info', className: 'flex items-center gap-2 min-w-0 flex-wrap' }, [
                    React.createElement('span', { className: 'font-medium text-sm truncate' }, v.name),
                    v.id === activeVoiceId && React.createElement('span', {
                      className: 'text-xs bg-primary text-primary-foreground rounded-full px-2 py-0.5 shrink-0'
                    }, 'Active'),
                    React.createElement('span', {
                      className: 'text-xs text-muted-foreground shrink-0 font-mono'
                    }, `${meta.badge} ${meta.label}`),
                    React.createElement('span', {
                      className: 'text-xs text-muted-foreground/60 shrink-0'
                    }, meta.vram),
                    React.createElement('span', {
                      className: 'text-xs text-muted-foreground/50 shrink-0'
                    }, v.voice_type === 'preset' ? '⭐ Preset' : '🎙️ Cloned')
                  ]),
                  // Right: delete
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
                ]);
              })
            )
      ]),

      // ── Clone New Voice ──────────────────────
      React.createElement('div', { key: 'cloner-card', className: 'flex flex-col gap-4 bg-card border border-border rounded-lg p-6 shadow-sm' }, [
        React.createElement('h2', { className: 'text-xl font-semibold' }, 'Clone New Voice'),

        // Engine selector
        React.createElement('div', { className: 'flex flex-col gap-2' }, [
          React.createElement('label', { className: 'text-sm font-medium text-muted-foreground' }, 'Cloning Model'),
          React.createElement(Select, { value: cloneEngine, onValueChange: setCloneEngine },
            React.createElement(SelectTrigger, { className: 'w-full h-10' }, [
              React.createElement(SelectValue, { placeholder: 'Select cloning engine...' })
            ]),
            React.createElement(SelectContent, {},
              CLONING_ENGINE_OPTIONS.map(opt =>
                React.createElement(SelectItem, { key: opt.value, value: opt.value },
                  `${opt.badge} ${opt.label}  •  ${opt.vram}  —  ${opt.note}`
                )
              )
            )
          ),
          React.createElement('div', {
            className: `text-xs rounded-md px-3 py-2 mt-1 border ${
              cloneEngine === 'qwen' ? 'border-red-500/30 bg-red-500/5 text-red-400'
              : 'border-yellow-500/30 bg-yellow-500/5 text-yellow-400'
            }`
          },
            `${selectedEngineMeta.badge || '⚪'} ${selectedEngineMeta.description || ''}`
          )
        ]),

        // Voice name
        React.createElement('div', { className: 'flex flex-col gap-2' }, [
          React.createElement('label', { className: 'text-sm font-medium text-muted-foreground' }, 'Voice Profile Name'),
          React.createElement(Input, {
            value: cloneName, placeholder: 'e.g., My Voice',
            onChange: (e) => setCloneName(e.target.value)
          })
        ]),

        // Reference text
        React.createElement('div', { className: 'flex flex-col gap-2 bg-muted/30 border border-border/50 rounded p-4' }, [
          React.createElement('span', { className: 'text-xs font-bold text-muted-foreground uppercase tracking-wider' }, 'Read this aloud:'),
          React.createElement('p', { className: 'text-lg italic font-medium leading-relaxed text-foreground' }, referenceText),
          React.createElement(Input, {
            className: 'text-xs mt-2 text-muted-foreground bg-transparent border-none p-0 h-auto focus-visible:ring-0',
            value: referenceText, placeholder: 'Edit reference text if needed...',
            onChange: (e) => setReferenceText(e.target.value)
          })
        ]),

        // Guidelines
        React.createElement('div', { className: 'flex flex-col gap-2 bg-muted/20 border border-border/50 rounded-md p-4 text-xs text-muted-foreground' }, [
          React.createElement('span', { className: 'font-semibold text-foreground text-sm' }, '🎙️ Voice Cloning Guidelines'),
          React.createElement('ul', { className: 'list-disc pl-4 flex flex-col gap-1' }, [
            React.createElement('li', {}, 'Formats: .wav, .mp3, .m4a, .ogg, .flac, .aac, .webm, .opus — max 50 MB.'),
            React.createElement('li', {}, 'Best results: 10–30s of clean audio with little background noise.'),
            React.createElement('li', {}, 'IMPORTANT: The reference text above must exactly match what was spoken in the audio.')
          ])
        ]),

        // Hidden file input
        React.createElement('input', {
          type: 'file', accept: 'audio/*', id: 'voice-file-upload',
          style: { display: 'none' }, onChange: handleFileUpload
        }),

        // Recording controls
        React.createElement('div', { className: 'flex items-center gap-3 mt-1' }, [
          recordingState === 'idle' && React.createElement(React.Fragment, { key: 'idle' }, [
            React.createElement(Button, { key: 'rec', variant: 'destructive', onClick: startRecording }, '🔴 Start Recording'),
            React.createElement(Button, { key: 'upl', variant: 'outline',
              onClick: () => document.getElementById('voice-file-upload').click() }, '📁 Upload Audio File')
          ]),
          recordingState === 'recording' && React.createElement(Button, {
            key: 'stop', variant: 'destructive', onClick: stopRecording }, '⏹️ Stop Recording'),
          recordingState === 'recorded' && React.createElement(React.Fragment, { key: 'recorded' }, [
            React.createElement(Button, { key: 'rerec', variant: 'outline', onClick: startRecording }, '🔄 Record Again'),
            React.createElement(Button, { key: 'reupl', variant: 'outline',
              onClick: () => document.getElementById('voice-file-upload').click() }, '📁 Upload New File'),
            React.createElement(Button, { key: 'play', variant: 'secondary', onClick: playPlayback },
              isPlaying ? '⏸️ Pause' : '▶️ Play Sample')
          ])
        ]),

        // Clone button
        React.createElement(Button, {
          className: 'w-full mt-4 h-11 text-base font-semibold', variant: 'default',
          disabled: isSaving || !cloneName.trim() || !audioBlob,
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
