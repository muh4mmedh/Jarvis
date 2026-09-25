/* ══════════════════════════════════════════════════════════
   J.A.R.V.I.S. — speech, wake word, camera

   Speech out : neural voice from the server, browser voices as fallback.
   Speech in  : push-to-talk, or always-on "hey jarvis".
   Camera     : captured in the browser so the user gets a real permission
                prompt and can see exactly what was sent.
   ══════════════════════════════════════════════════════════ */
'use strict';

const audioOut = new Audio();
audioOut.preload = 'auto';

/* ══ CHIME ═════════════════════════════════════════════════
   Two-tone acknowledgement when the wake word lands. Synthesised
   rather than shipped as a file — no asset, no licence question. */
let actx = null;
function chime(up = true) {
  if (!S.boot?.voice?.chime) return;
  try {
    actx ||= new (window.AudioContext || window.webkitAudioContext)();
    const now = actx.currentTime;
    [[880, 0], [1320, 0.09]].forEach(([freq, delay], i) => {
      const osc = actx.createOscillator();
      const gain = actx.createGain();
      osc.type = 'sine';
      osc.frequency.value = up ? freq : freq * 0.6;
      gain.gain.setValueAtTime(0, now + delay);
      gain.gain.linearRampToValueAtTime(0.07, now + delay + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + delay + 0.16);
      osc.connect(gain).connect(actx.destination);
      osc.start(now + delay);
      osc.stop(now + delay + 0.18);
    });
  } catch { /* audio unavailable — the visual state is enough */ }
}

/* ══ SPEECH OUT ════════════════════════════════════════════ */
function stripForSpeech(text) {
  return (text || '')
    .replace(/```[\s\S]*?```/g, ' — code omitted — ')
    .replace(/`([^`]*)`/g, '$1')
    // Markdown links first — otherwise the URL rule eats the target and
    // leaves a broken "[text](a link" behind.
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/[^\s)\]]+/g, 'a link')
    .replace(/[*_#>|]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function pickBrowserVoice() {
  const voices = speechSynthesis.getVoices();
  if (!voices.length) return null;
  for (const want of (S.boot?.voice?.prefer || [])) {
    const hit = voices.find((v) => v.name.toLowerCase().includes(String(want).toLowerCase()));
    if (hit) return hit;
  }
  return voices.find((v) => v.lang === 'en-GB' && /male|george|ryan|daniel|thomas/i.test(v.name))
      || voices.find((v) => v.lang === 'en-GB')
      || voices.find((v) => v.lang.startsWith('en'))
      || voices[0];
}

function browserSpeak(clean) {
  if (!window.speechSynthesis) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(clean);
  const v = pickBrowserVoice();
  if (v) { u.voice = v; u.lang = v.lang; }
  u.rate = S.voiceRate;
  u.pitch = S.voicePitch;
  u.onstart = () => beginSpeaking();
  u.onend = u.onerror = () => endSpeaking();
  speechSynthesis.speak(u);
}

/* The recogniser is deaf while JARVIS talks.
   Flagging `S.speaking` was not enough on its own: recognition keeps running
   through playback and delivers its transcript a beat later, by which time the
   flag has already cleared — so JARVIS heard itself and answered. The stream is
   now actually stopped, and stays stopped for a moment after the audio ends to
   swallow the tail and any room echo. */
let resumeTimer = null;

function beginSpeaking() {
  S.speaking = true;
  setState('speaking');
  clearTimeout(resumeTimer);
  if ((S.listening || S.ambient) && S.recog) {
    S.micSuspended = true;
    try { S.recog.abort(); } catch {}
  }
}

function endSpeaking() {
  S.speaking = false;
  if (S.state === 'speaking') setState('standby');
  clearTimeout(resumeTimer);
  // Long enough for the speakers to fall silent and the room to settle.
  resumeTimer = setTimeout(() => {
    S.micSuspended = false;
    if (S.listening || S.ambient) startRecognition();
  }, 700);
}

async function speak(text) {
  if (!S.voiceOn) return;
  const clean = stripForSpeech(text).slice(0, 3000);
  if (!clean) return;

  stopSpeaking();

  if ((S.boot?.voice?.engine || 'neural') !== 'neural') {
    browserSpeak(clean);
    return;
  }

  // Neural voice from the server. Any failure at all — offline, service
  // hiccup, edge-tts missing — quietly falls back to the OS voices so
  // speech never just stops working.
  try {
    const url = await NET.speech(clean);
    audioOut.src = url;
    audioOut.onplay = beginSpeaking;
    audioOut.onended = audioOut.onerror = () => {
      endSpeaking();
      URL.revokeObjectURL(url);
    };
    await audioOut.play();
  } catch (err) {
    console.warn('neural voice unavailable, using browser voice:', err.message);
    browserSpeak(clean);
  }
}

function stopSpeaking() {
  try { audioOut.pause(); audioOut.currentTime = 0; } catch {}
  if (window.speechSynthesis) speechSynthesis.cancel();
  endSpeaking();
}

/* ══ SPEECH IN ═════════════════════════════════════════════ */
const wakeWord = () => (S.boot?.voice?.wake_word || 'jarvis').toLowerCase();

// Matches "jarvis", "hey jarvis", "ok jarvis", "hi jarvis" and eats the
// punctuation and filler that follows.
function wakeRegex() {
  const w = wakeWord().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?:^|\\b)(?:hey|hi|ok|okay|yo)?[\\s,]*${w}\\b[\\s,.!?]*`, 'i');
}

let followUpUntil = 0;   // after a bare "hey jarvis", accept the next phrase

/* Recognition marks a result "final" after a beat of quiet, which is far
   shorter than a person pausing to think. Sentences were being sent the moment
   you hesitated. Finals are now accumulated and only committed once you have
   genuinely stopped talking. */
const SPEECH = { buffer: '', timer: null, silenceMs: 1900 };

function queueSpeech(text) {
  SPEECH.buffer = (SPEECH.buffer ? SPEECH.buffer + ' ' : '') + text.trim();
  clearTimeout(SPEECH.timer);

  const remaining = SPEECH.silenceMs;
  setHint(`Listening… "${SPEECH.buffer.slice(-58)}"`);
  $('#input').value = SPEECH.buffer;
  if (typeof autoGrow === 'function') autoGrow();

  SPEECH.timer = setTimeout(commitSpeech, remaining);
}

function commitSpeech() {
  clearTimeout(SPEECH.timer);
  const text = SPEECH.buffer.trim();
  SPEECH.buffer = '';
  setHint('');
  if (!text) return;
  $('#input').value = '';
  if (typeof autoGrow === 'function') autoGrow();
  dispatchSpeech(text);
}

function discardSpeech() {
  clearTimeout(SPEECH.timer);
  SPEECH.buffer = '';
}

function handleTranscript(final) {
  const text = final.trim();
  if (!text) return;

  // Never let JARVIS answer its own voice, including a transcript that
  // arrives just after playback stopped.
  if (S.micSuspended) return;
  if (Date.now() - (S.spokeUntil || 0) < 700) return;

  // Speaking over JARVIS interrupts it rather than being ignored.
  if (S.speaking) stopSpeaking();

  queueSpeech(text);
}

/* Runs once the speaker has actually finished. */
function dispatchSpeech(text) {
  const re = wakeRegex();
  const heard = re.test(text);

  if (S.ambient) {
    const now = Date.now();
    if (!heard && now > followUpUntil) return;      // not addressed to us

    const command = heard ? text.replace(re, '').trim() : text;
    if (!command) {
      // Bare "hey jarvis" — acknowledge and listen for the actual request.
      chime(true);
      followUpUntil = now + 8000;
      setHint(`Listening, ${S.boot?.identity?.address || 'Sir'}…`);
      return;
    }
    chime(true);
    followUpUntil = 0;
    send(command);
    return;
  }

  // Push-to-talk: everything counts, wake word optional.
  send(heard ? text.replace(re, '').trim() || text : text);
}

function initSpeech() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    setHint('Voice input needs Chrome or Edge — typing still works.');
    return;
  }

  const r = new SR();
  r.continuous = true;
  r.interimResults = true;
  r.lang = 'en-GB';

  r.onresult = (e) => {
    let interim = '', final = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i][0].transcript;
      e.results[i].isFinal ? (final += t) : (interim += t);
    }
    if (interim) {
      // Still talking — hold the commit timer open.
      if (SPEECH.buffer || !S.ambient) {
        clearTimeout(SPEECH.timer);
        SPEECH.timer = setTimeout(commitSpeech, SPEECH.silenceMs);
        $('#input').value = (SPEECH.buffer ? SPEECH.buffer + ' ' : '') + interim;
        if (typeof autoGrow === 'function') autoGrow();
      }
      if (S.ambient && wakeRegex().test(interim)) $('#mic').classList.add('heard');
    }
    if (final) {
      $('#mic').classList.remove('heard');
      handleTranscript(final);
    }
  };

  // The API stops itself periodically; restart it so "always on" really is.
  r.onend = () => {
    if (S.micSuspended) return;          // deliberately stopped while speaking
    if (S.listening || S.ambient) {
      try { r.start(); } catch { setTimeout(() => { try { r.start(); } catch {} }, 400); }
    }
  };

  r.onerror = (e) => {
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
      S.listening = S.ambient = false;
      $('#mic').classList.remove('live', 'ambient');
      setHint('Microphone blocked — allow access from the padlock in the address bar.');
    } else if (e.error === 'no-speech' || e.error === 'aborted') {
      // Routine in always-on mode; onend restarts us.
    } else {
      console.warn('speech recognition:', e.error);
    }
  };

  S.recog = r;

  if (S.boot?.voice?.always_listening) setAmbient(true);
}

function setHint(text) {
  $('#hint').textContent = text;
}

function startRecognition() {
  if (!S.recog || S.micSuspended) return false;
  try { S.recog.start(); } catch { /* already running */ }
  return true;
}

function setAmbient(on) {
  S.ambient = on;
  $('#mic').classList.toggle('ambient', on);
  if (on) {
    S.listening = false;
    $('#mic').classList.remove('live');
    startRecognition();
    setHint(`Always listening — say "hey ${wakeWord()}".`);
  } else {
    if (!S.listening) { try { S.recog?.stop(); } catch {} }
    $('#mic').classList.remove('heard');
    setHint('');
  }
}

/* One button, two states: listening or not.

   It used to arm a single utterance, which meant the wake word had nothing
   running to hear it — "hey JARVIS" only worked if you had already turned on
   always-listening in Settings, which is backwards. Clicking the microphone
   now opens the wake-word listener, which is what people expect. */
function toggleMic() {
  if (!S.recog) {
    setHint('Voice input is unavailable in this window.');
    return;
  }
  setAmbient(!S.ambient);
  if (S.ambient) {
    NET.saveSettings({ 'voice.always_listening': true }).catch(() => {});
  } else {
    discardSpeech();
    NET.saveSettings({ 'voice.always_listening': false }).catch(() => {});
  }
}

/* ══ CAMERA ════════════════════════════════════════════════
   The stream is opened once and kept warm. Re-opening the device for
   every look cost about a second each time — the sensor needs a moment
   to expose or the frame comes back black — which made JARVIS feel slow
   and made a sequence of frames impossible. It now holds the stream,
   grabs instantly, shows the user a live preview while it is open, and
   releases the device after a spell of not being used. */
const CAM = {
  stream: null,
  video: null,
  idleTimer: null,
  opening: null,
  IDLE_MS: 45000,
};

async function ensureCamera() {
  if (CAM.stream && CAM.stream.active) {
    armCameraIdle();
    return CAM.video;
  }
  if (CAM.opening) return CAM.opening;

  CAM.opening = (async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('this window exposes no camera API');
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' },
        audio: false,
      });
    } catch (err) {
      const reasons = {
        NotAllowedError: 'camera permission was denied',
        NotFoundError: 'no camera is connected',
        NotReadableError: 'the camera is already in use by another application',
        OverconstrainedError: 'no camera matches the requested settings',
        SecurityError: 'camera access is blocked by policy',
      };
      throw new Error(reasons[err.name] || err.message || 'the camera could not be opened');
    }

    const video = document.createElement('video');
    video.srcObject = stream;
    video.muted = true;
    video.playsInline = true;
    await video.play();

    // One warm-up wait, not one per frame.
    await new Promise((r) => setTimeout(r, 600));

    CAM.stream = stream;
    CAM.video = video;
    showCameraPreview(true);
    armCameraIdle();
    return video;
  })();

  try {
    return await CAM.opening;
  } finally {
    CAM.opening = null;
  }
}

function armCameraIdle() {
  clearTimeout(CAM.idleTimer);
  CAM.idleTimer = setTimeout(releaseCamera, CAM.IDLE_MS);
}

function releaseCamera() {
  clearTimeout(CAM.idleTimer);
  if (CAM.stream) CAM.stream.getTracks().forEach((t) => t.stop());
  if (CAM.video) CAM.video.srcObject = null;
  CAM.stream = CAM.video = null;
  showCameraPreview(false);
}

/* Gemini tiles images into 768x768 crops at 258 tokens each, so a 1280px
   frame costs two tiles while a 768px frame costs one — the same picture for
   half the tokens. Anything wider than 768 is scaled down before encoding. */
const TILE = 768;

function grabFrame(quality = 0.82, maxEdge = 0) {
  const v = CAM.video;
  if (!v) throw new Error('camera is not open');
  let w = v.videoWidth || 1280;
  let h = v.videoHeight || 720;

  if (maxEdge && Math.max(w, h) > maxEdge) {
    const k = maxEdge / Math.max(w, h);
    w = Math.round(w * k);
    h = Math.round(h * k);
  }

  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(v, 0, 0, w, h);
  return {
    image: canvas.toDataURL('image/jpeg', quality).split(',')[1],
    resolution: `${w}x${h}`,
    _canvas: canvas,
  };
}

/* A cheap perceptual fingerprint: 32x18 greyscale. Comparing these lets us
   drop frames where nothing actually moved, which is the common case when
   someone is sitting still — no point paying 258 tokens to send the same
   picture four times. */
function fingerprint(canvas) {
  const c = document.createElement('canvas');
  c.width = 32;
  c.height = 18;
  const ctx = c.getContext('2d');
  ctx.drawImage(canvas, 0, 0, 32, 18);
  const px = ctx.getImageData(0, 0, 32, 18).data;
  const out = new Uint8Array(32 * 18);
  for (let i = 0, j = 0; i < px.length; i += 4, j++) {
    out[j] = (px[i] * 0.299 + px[i + 1] * 0.587 + px[i + 2] * 0.114) | 0;
  }
  return out;
}

function frameDelta(a, b) {
  if (!a || !b) return 255;
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += Math.abs(a[i] - b[i]);
  return sum / a.length;          // mean absolute difference, 0-255
}

/* One frame, right now. */
async function captureCameraFrame(detail = false) {
  await ensureCamera();
  // A single glance fits one tile too; `detail` doubles it for reading a
  // label or small print, at twice the tokens.
  const shot = grabFrame(detail ? 0.85 : 0.8, detail ? 1280 : TILE);
  flashCameraPreview();
  delete shot._canvas;
  return shot;
}

/* Several frames spread over time, so JARVIS can see motion and sequence
   rather than a single instant.

   Two economies, because each frame is billed: every frame is scaled to one
   768px tile, and frames that look the same as the one before are dropped. A
   still scene costs two frames instead of five; a busy one still sends the
   lot. */
const STILL_THRESHOLD = 3.2;      // mean grey difference below this = no motion

async function captureCameraClip(frames = 4, intervalMs = 800) {
  await ensureCamera();
  const n = Math.max(2, Math.min(frames, 10));
  const gap = Math.max(200, Math.min(intervalMs, 3000));

  const kept = [];
  let lastPrint = null;
  let skipped = 0;

  for (let i = 0; i < n; i++) {
    const shot = grabFrame(0.72, TILE);
    const print = fingerprint(shot._canvas);
    const moved = frameDelta(print, lastPrint);

    // Always keep the first and last; drop the static middle.
    if (i === 0 || i === n - 1 || moved >= STILL_THRESHOLD) {
      kept.push(shot.image);
      lastPrint = print;
    } else {
      skipped += 1;
    }

    setHint(`Watching… ${i + 1}/${n}`);
    if (i < n - 1) await new Promise((r) => setTimeout(r, gap));
  }

  flashCameraPreview();
  setHint('');
  const meta = grabFrame(0.5, TILE);
  return {
    images: kept,
    count: kept.length,
    captured: n,
    skipped_static: skipped,
    resolution: meta.resolution,
    span_seconds: +(((n - 1) * gap) / 1000).toFixed(1),
  };
}

/* A live preview while the camera is open — the user should never have to
   wonder whether it is on, or what it can see. */
function showCameraPreview(on) {
  const host = $('#campreview');
  if (!host) return;
  host.classList.toggle('hidden', !on);
  const slot = $('#campreview-video');
  if (on && CAM.video && slot && !slot.contains(CAM.video)) {
    slot.innerHTML = '';
    CAM.video.style.width = '100%';
    CAM.video.style.display = 'block';
    slot.appendChild(CAM.video);
  }
  if (!on && slot) slot.innerHTML = '';
}

function flashCameraPreview() {
  const host = $('#campreview');
  if (!host) return;
  host.classList.add('shot');
  setTimeout(() => host.classList.remove('shot'), 260);
}

$('#cam-close')?.addEventListener('click', releaseCamera);

/* ══ REQUESTS FROM SERVER-SIDE TOOLS ═══════════════════════ */
async function handleClientRequest(msg) {
  setState('executing');
  try {
    let data;
    if (msg.kind === 'camera_frame') {
      setHint('Looking…');
      data = await captureCameraFrame(!!msg.detail);
    } else if (msg.kind === 'camera_clip') {
      data = await captureCameraClip(msg.frames, msg.interval_ms);
    } else {
      tx({ type: 'client_response', id: msg.id, error: `unknown request: ${msg.kind}` });
      return;
    }
    tx({ type: 'client_response', id: msg.id, data });
  } catch (err) {
    tx({ type: 'client_response', id: msg.id, error: err.message });
  } finally {
    setHint('');
  }
}
