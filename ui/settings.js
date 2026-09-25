/* ══════════════════════════════════════════════════════════
   J.A.R.V.I.S. — Settings

   Everything configurable lives here. Nothing is baked into the
   source: the API key, model, persona, thresholds and theme all
   come from /api/settings and persist to data/settings.json,
   which is git-ignored. A fresh clone starts empty and asks.
   ══════════════════════════════════════════════════════════ */
'use strict';

let SETTINGS = null;

async function loadSettings() {
  SETTINGS = await NET.settings();
  const g = (p, d) => p.split('.').reduce((o, k) => (o ?? {})[k], SETTINGS) ?? d;

  // ── API ──
  $('#s-key').value = '';
  $('#s-key-hint').textContent = g('secrets.gemini_api_key_set')
    ? `Currently set: ${g('secrets.gemini_api_key_hint')}` +
      (g('secrets.from_env') ? ' (supplied by .env)' : '')
    : 'No key configured yet.';
  $('#s-model').value  = g('model.name', '');
  $('#s-temp').value   = g('model.temperature', 0.85);
  $('#s-maxtok').value = g('model.max_output_tokens', 8192);
  $('#s-steps').value  = g('model.max_tool_steps', 12);

  // ── Persona ──
  $('#s-name').value      = g('identity.name', 'JARVIS');
  $('#s-address').value   = g('identity.address_user_as', 'Sir');
  $('#s-expansion').value = g('identity.expansion', '');
  $('#s-wit').value       = g('identity.wit', 6);
  $('#s-wit-val').textContent = g('identity.wit', 6);
  $('#s-mem-enabled').value   = String(g('memory.enabled', true));
  $('#s-turns').value         = g('memory.context_turns', 24);

  // ── Security ──
  $('#s-mode').value      = g('security.mode', 'guarded');
  $('#s-timeout').value   = g('security.shell_timeout', 45);
  $('#s-maxout').value    = g('security.max_tool_output', 24000);
  $('#s-protected').value = (g('security.protected_paths', []) || []).join('\n');

  // ── Voice ──
  $('#s-tts').value    = String(g('voice.tts_enabled', true));
  $('#s-stt').value    = String(g('voice.stt_enabled', true));
  $('#s-engine').value = g('voice.engine', 'gemini');
  $('#s-style').value = g('voice.style', '');
  populateGeminiVoices(g('voice.gemini_voice', 'Charon'));
  $('#s-always').value = String(g('voice.always_listening', false));
  $('#s-chime').value  = String(g('voice.chime', true));
  $('#s-wake').value   = g('voice.wake_word', 'jarvis');
  $('#s-wake-echo').textContent = g('voice.wake_word', 'jarvis');
  loadNeuralVoices(g('voice.neural_voice', 'en-GB-RyanNeural'));
  syncVoiceRows();
  $('#s-rate').value  = g('voice.rate', 1.04);
  $('#s-pitch').value = g('voice.pitch', 0.92);
  $('#s-rate-val').textContent  = g('voice.rate', 1.04);
  $('#s-pitch-val').textContent = g('voice.pitch', 0.92);
  populateVoices(g('voice.voice_prefer', []));

  // ── Interface ──
  $('#s-accent').value    = g('ui.theme_accent', '#38e0ff');
  $('#s-warn').value      = g('ui.theme_warn', '#ffb648');
  $('#s-alert').value     = g('ui.theme_alert', '#ff4d5a');
  $('#s-boot').value      = String(g('ui.boot_sequence', true));
  $('#s-telemetry').value = g('ui.telemetry_interval', 2.0);
  $('#s-window').value    = g('server.window', 'app');
  $('#s-port').value      = g('server.port', 8765);
}

function populateVoices(prefer) {
  const sel = $('#s-voice');
  if (!sel) return;
  const voices = window.speechSynthesis ? speechSynthesis.getVoices() : [];
  const current = (prefer || [])[0] || '';
  sel.innerHTML = '<option value="">Automatic (British male if available)</option>' +
    voices.map((v) => `<option value="${v.name}">${v.name} — ${v.lang}</option>`).join('');
  sel.value = current;
}

if (window.speechSynthesis) {
  speechSynthesis.addEventListener('voiceschanged', () => {
    if (SETTINGS) populateVoices(SETTINGS.voice?.voice_prefer);
  });
}

function collectSettings() {
  const patch = {
    'model.name': $('#s-model').value.trim(),
    'model.temperature': parseFloat($('#s-temp').value),
    'model.max_output_tokens': parseInt($('#s-maxtok').value, 10),
    'model.max_tool_steps': parseInt($('#s-steps').value, 10),

    'identity.name': $('#s-name').value.trim() || 'JARVIS',
    'identity.address_user_as': $('#s-address').value.trim() || 'Sir',
    'identity.expansion': $('#s-expansion').value.trim(),
    'identity.wit': parseInt($('#s-wit').value, 10),
    'memory.enabled': $('#s-mem-enabled').value === 'true',
    'memory.context_turns': parseInt($('#s-turns').value, 10),

    'security.mode': $('#s-mode').value,
    'security.shell_timeout': parseInt($('#s-timeout').value, 10),
    'security.max_tool_output': parseInt($('#s-maxout').value, 10),
    'security.protected_paths': $('#s-protected').value
      .split('\n').map((s) => s.trim()).filter(Boolean),

    'voice.tts_enabled': $('#s-tts').value === 'true',
    'voice.stt_enabled': $('#s-stt').value === 'true',
    'voice.engine': $('#s-engine').value,
    'voice.gemini_voice': $('#s-gvoice').value,
    'voice.style': $('#s-style').value.trim(),
    'voice.neural_voice': $('#s-neural').value,
    'voice.always_listening': $('#s-always').value === 'true',
    'voice.chime': $('#s-chime').value === 'true',
    'voice.wake_word': $('#s-wake').value.trim() || 'jarvis',
    'voice.voice_prefer': $('#s-voice').value ? [$('#s-voice').value] : [],
    'voice.rate': parseFloat($('#s-rate').value),
    'voice.pitch': parseFloat($('#s-pitch').value),

    'ui.theme_accent': $('#s-accent').value,
    'ui.theme_warn': $('#s-warn').value,
    'ui.theme_alert': $('#s-alert').value,
    'ui.boot_sequence': $('#s-boot').value === 'true',
    'ui.telemetry_interval': parseFloat($('#s-telemetry').value),

    'server.window': $('#s-window').value,
    'server.port': parseInt($('#s-port').value, 10),
  };
  // Only transmit the key if a new one was actually typed.
  const key = $('#s-key').value.trim();
  if (key) patch['secrets.gemini_api_key'] = key;
  return patch;
}

async function saveSettings() {
  const note = $('#s-note');
  note.className = 'save-note';
  note.textContent = 'Saving…';

  let res;
  try {
    res = await NET.saveSettings(collectSettings());
  } catch {
    note.className = 'save-note';
    note.textContent = 'Could not reach the local server.';
    return;
  }

  SETTINGS = res.settings;
  applyTheme();

  // Push the live bits into the running session without a reload.
  S.boot.voice.wake_word = $('#s-wake').value.trim() || 'jarvis';
  S.boot.voice.engine = $('#s-engine').value;
  S.boot.voice.neural_voice = $('#s-neural').value;
  S.boot.voice.chime = $('#s-chime').value === 'true';
  setAmbient($('#s-always').value === 'true');
  S.boot.identity.address = $('#s-address').value.trim() || 'Sir';
  S.boot.voice.prefer = $('#s-voice').value ? [$('#s-voice').value] : [];
  S.voiceOn = $('#s-tts').value === 'true';
  S.voiceRate = parseFloat($('#s-rate').value);
  S.voicePitch = parseFloat($('#s-pitch').value);
  $('#btn-mute').textContent = `VOICE: ${S.voiceOn ? 'ON' : 'OFF'}`;
  $('#expansion').textContent = $('#s-expansion').value;
  if (res.model) $('#stat-model').textContent = res.model;
  setMode($('#s-mode').value, true);

  note.className = 'save-note ok';
  note.textContent = res.restart_required?.length
    ? `Saved. Restart needed for: ${res.restart_required.join(', ')}`
    : `Saved — ${res.changed.length} setting(s) updated.`;

  $('#s-key').value = '';
  if (res.settings?.secrets?.gemini_api_key_set) {
    $('#s-key-hint').textContent = `Currently set: ${res.settings.secrets.gemini_api_key_hint}`;
  }
  setTimeout(() => { note.textContent = ''; note.className = 'save-note'; }, 5000);
}

function applyTheme() {
  const root = document.documentElement.style;
  root.setProperty('--accent', $('#s-accent').value);
  root.setProperty('--warn', $('#s-warn').value);
  root.setProperty('--alert', $('#s-alert').value);
}

async function testKey(candidate, resultEl) {
  resultEl.className = 'test-result busy';
  resultEl.textContent = 'Contacting Google…';
  try {
    const res = await NET.testKey(candidate);

    if (res.ok) {
      resultEl.className = 'test-result ok';
      resultEl.textContent =
        `Key valid — ${res.model_count} models available. Would use: ${res.would_use}`;
      const dl = $('#model-list');
      if (dl) dl.innerHTML = (res.models || []).map((m) => `<option value="${m}">`).join('');
      return true;
    }
    resultEl.className = 'test-result bad';
    resultEl.textContent = res.error || 'Key rejected.';
    return false;
  } catch {
    resultEl.className = 'test-result bad';
    resultEl.textContent = 'Could not reach the local server.';
    return false;
  }
}

/* ── settings wiring ─────────────────────────────────────── */
$('#btn-settings').onclick = async () => {
  await loadSettings();
  $('#settings').classList.remove('hidden');
};
const closeSettings = () => $('#settings').classList.add('hidden');
$('#set-close').onclick = closeSettings;
$('#s-cancel').onclick  = closeSettings;
$('#s-save').onclick    = saveSettings;
$('#s-test').onclick    = () => testKey($('#s-key').value.trim(), $('#s-test-result'));

$('#s-wit').oninput   = (e) => ($('#s-wit-val').textContent = e.target.value);
$('#s-rate').oninput  = (e) => ($('#s-rate-val').textContent = e.target.value);
$('#s-pitch').oninput = (e) => ($('#s-pitch-val').textContent = e.target.value);
['#s-accent', '#s-warn', '#s-alert'].forEach((s) => ($(s).oninput = applyTheme));

$('#s-voice-test').onclick = async () => {
  const line = `Good evening, ${$('#s-address').value || 'Sir'}. ` +
               'All systems are functioning within normal parameters.';
  const result = $('#s-voice-result');

  if ($('#s-engine').value === 'browser') {
    const u = new SpeechSynthesisUtterance(line);
    const v = speechSynthesis.getVoices().find((x) => x.name === $('#s-voice').value);
    if (v) { u.voice = v; u.lang = v.lang; }
    u.rate = parseFloat($('#s-rate').value);
    u.pitch = parseFloat($('#s-pitch').value);
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
    result.className = 'test-result';
    result.textContent = '';
    return;
  }

  // Save the voice choice first so the server renders with what you picked.
  result.className = 'test-result busy';
  result.textContent = 'synthesising…';
  await NET.saveSettings({
    'voice.neural_voice': $('#s-neural').value,
    'voice.rate': parseFloat($('#s-rate').value),
    'voice.pitch': parseFloat($('#s-pitch').value),
  });
  try {
    const audio = new Audio(await NET.speech(line));
    await audio.play();
    result.className = 'test-result ok';
    result.textContent = $('#s-neural').value;
  } catch (err) {
    result.className = 'test-result bad';
    result.textContent = err.message;
  }
};

$$('.set-tab').forEach((tab) => (tab.onclick = () => {
  $$('.set-tab').forEach((t) => t.classList.toggle('active', t === tab));
  $$('.set-pane').forEach((p) => p.classList.toggle('active', p.dataset.pane === tab.dataset.tab));
}));

/* ── first-run wiring ────────────────────────────────────── */
$('#setup-save').onclick = async () => {
  const key = $('#setup-key').value.trim();
  if (!key) {
    $('#setup-result').className = 'test-result bad';
    $('#setup-result').textContent = 'Paste a key first.';
    return;
  }
  if (!await testKey(key, $('#setup-result'))) return;

  const res = await NET.saveSettings({ 'secrets.gemini_api_key': key });
  if (res.model) $('#stat-model').textContent = res.model;

  $('#setup').classList.add('hidden');
  S.boot.api_key_present = true;
  const greet = `Key accepted. All systems online, ${S.boot.identity?.address || 'Sir'}. How may I help?`;
  addMessage('jarvis', 'J.A.R.V.I.S.', greet);
  speak(greet);
};

$('#setup-skip').onclick = () => {
  $('#setup').classList.add('hidden');
  addMessage('system', 'SYSTEM',
    'Running without an API key — telemetry works, but I cannot converse. ' +
    'Add a key from the settings icon at the top right whenever you are ready.');
};

$('#setup-key').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') $('#setup-save').click();
});


/* ── neural voice catalogue ──────────────────────────────── */
let NEURAL_LOADED = false;

async function loadNeuralVoices(selected) {
  const sel = $('#s-neural');
  const status = $('#s-neural-status');
  if (NEURAL_LOADED) { sel.value = selected; return; }

  status.className = 'test-result busy';
  status.textContent = 'loading…';
  try {
    const res = await NET.voices();
    if (!res.ok) throw new Error(res.error || 'unavailable');

    sel.innerHTML = res.voices.map((v) =>
      `<option value="${v.id}">${v.recommended ? '★ ' : ''}${v.id} — ` +
      `${v.locale} ${v.gender}${v.traits ? ' · ' + v.traits : ''}</option>`).join('');
    sel.value = selected;
    if (!sel.value && res.voices.length) sel.value = res.voices[0].id;

    NEURAL_LOADED = true;
    status.className = 'test-result ok';
    status.textContent = `${res.voices.length} available`;
  } catch (err) {
    status.className = 'test-result bad';
    status.textContent = err.message;
    sel.innerHTML = `<option value="${selected}">${selected}</option>`;
  }
}

/* Only show the selector that applies to the chosen engine. */
function syncVoiceRows() {
  const engine = $('#s-engine').value;
  $('#row-gemini').classList.toggle('hidden', engine !== 'gemini');
  $('#row-style').classList.toggle('hidden', engine !== 'gemini');
  $('#row-neural').classList.toggle('hidden', engine !== 'neural');
  $('#row-osvoice').classList.toggle('hidden', engine !== 'browser');
}

/* Gemini's prebuilt voices. Deeper, measured ones first. */
const GEMINI_VOICES = [
  ['Charon', 'deep, measured — closest to the films'],
  ['Orus', 'firm and level'],
  ['Algieba', 'smooth, low'],
  ['Enceladus', 'breathy, quieter'],
  ['Iapetus', 'clear and even'],
  ['Umbriel', 'easy-going'],
  ['Rasalgethi', 'informative'],
  ['Schedar', 'even'],
  ['Gacrux', 'mature'],
  ['Puck', 'brighter, animated'],
  ['Zephyr', 'bright'],
  ['Fenrir', 'excitable'],
  ['Kore', 'firm, female'],
  ['Leda', 'youthful, female'],
  ['Aoede', 'breezy, female'],
  ['Achernar', 'soft, female'],
];

function populateGeminiVoices(selected) {
  const sel = $('#s-gvoice');
  if (!sel) return;
  sel.innerHTML = GEMINI_VOICES.map(([id, desc], i) =>
    `<option value="${id}">${i < 3 ? '★ ' : ''}${id} — ${desc}</option>`).join('');
  sel.value = selected || 'Charon';
}

$('#s-engine').onchange = () => {
  syncVoiceRows();
  if ($('#s-engine').value === 'neural') loadNeuralVoices($('#s-neural').value);
};

$('#s-wake').oninput = (e) => {
  $('#s-wake-echo').textContent = e.target.value.trim() || 'jarvis';
};


/* ── face enrolment ──────────────────────────────────────── */
async function refreshFaceState() {
  const el = $('#s-face-state');
  if (!el) return;
  try {
    // Ask JARVIS itself rather than adding another endpoint.
    const res = await NET.faceStatus();
    if (res.enrolled) {
      el.className = 'test-result ok';
      el.textContent = `enrolled${res.name ? ' · ' + res.name : ''} · ${res.samples} samples`;
    } else {
      el.className = 'test-result';
      el.textContent = res.models_downloaded ? 'no face enrolled' : 'models not downloaded';
    }
  } catch {
    el.textContent = '';
  }
}

$('#s-face-enrol').onclick = async () => {
  const result = $('#s-face-result');
  const btn = $('#s-face-enrol');
  const who = ($('#s-address').value || 'Sir').trim();

  btn.disabled = true;
  result.className = 'test-result busy';
  result.textContent = 'Look at the camera and hold still…';

  try {
    const res = await NET.enrolFace(who, 5);
    if (res?.ok) {
      result.className = 'test-result ok';
      result.textContent =
        `Enrolled — ${res.samples} samples` +
        (res.rejected ? ` (${res.rejected} frames had no clear face)` : '');
      refreshFaceState();
    } else {
      result.className = 'test-result bad';
      result.textContent = res?.error || 'Enrolment failed.';
    }
  } catch (err) {
    result.className = 'test-result bad';
    result.textContent = err.message || 'Enrolment failed.';
  } finally {
    btn.disabled = false;
  }
};

$('#s-face-forget').onclick = async () => {
  const result = $('#s-face-result');
  await NET.forgetFace();
  result.className = 'test-result';
  result.textContent = 'Face signature deleted.';
  refreshFaceState();
};
