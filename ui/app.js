/* ══════════════════════════════════════════════════════════
   J.A.R.V.I.S. — HUD client
   ══════════════════════════════════════════════════════════ */
'use strict';

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const S = {
  ws: null,
  boot: null,          // /api/boot payload
  state: 'standby',    // standby | thinking | executing | awaiting | speaking
  streaming: null,     // live <div class="body"> being written into
  streamText: '',
  tokens: 0,
  turnStart: 0,
  voiceOn: true,
  voiceRate: 1.04,
  voicePitch: 0.92,
  listening: false,
  ambient: false,
  speaking: false,
  micSuspended: false,
  spokeUntil: 0,
  recog: null,
  retry: 0,
  native: false,      // running inside the native window shell
};

/* ══ BOOT SEQUENCE ═════════════════════════════════════════ */
const BOOT_LINES = [
  ['Initialising kernel interface', 90],
  ['Mounting local filesystem bridge', 70],
  ['Arc reactor at <b>100%</b> capacity', 110],
  ['Loading capability registry', 80],
  ['Establishing uplink to language core', 120],
  ['Calibrating telemetry sensors', 70],
  ['Engaging permission subsystem', 90],
  ['', 40],
  ['<b>All systems nominal.</b>', 260],
  ['Good to see you again, <b>Sir</b>.', 420],
];

async function runBoot() {
  const log = $('#boot-log');
  const skip = !(S.boot?.ui?.boot_sequence ?? true);
  if (skip) { finishBoot(); return; }

  for (const [text, wait] of BOOT_LINES) {
    if (text) {
      const ok = text.includes('Sir') || text.includes('nominal') ? '' : ' <span class="ok">[ OK ]</span>';
      log.innerHTML += `  ${text}${text.includes('<b>All') || text.includes('Sir') ? '' : ok}\n`;
    } else {
      log.innerHTML += '\n';
    }
    log.scrollTop = log.scrollHeight;
    await sleep(wait);
  }
  await sleep(500);
  finishBoot();
}

function finishBoot() {
  $('#boot').classList.add('out');
  $('#app').classList.remove('hidden');
  setTimeout(() => $('#boot').classList.add('hidden'), 800);
  $('#input').focus();
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ══ ARC REACTOR ═══════════════════════════════════════════ */
const reactor = (() => {
  const cv = $('#reactor');
  const ctx = cv.getContext('2d');
  let t = 0, energy = 0, targetEnergy = 0.25, flash = 0;

  const PALETTE = {
    standby:   ['#38e0ff', 0.25],
    thinking:  ['#38e0ff', 0.85],
    executing: ['#ffb648', 1.00],
    awaiting:  ['#ffb648', 0.70],
    speaking:  ['#38e0ff', 0.60],
    error:     ['#ff4d5a', 1.00],
  };

  function size() {
    const dpr = window.devicePixelRatio || 1;
    cv.width = 260 * dpr; cv.height = 260 * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  size();
  window.addEventListener('resize', size);

  function ring(cx, cy, r, segs, gap, rot, color, alpha, width) {
    ctx.save();
    ctx.strokeStyle = color; ctx.globalAlpha = alpha; ctx.lineWidth = width;
    ctx.lineCap = 'butt';
    const step = (Math.PI * 2) / segs;
    for (let i = 0; i < segs; i++) {
      const a0 = rot + i * step;
      ctx.beginPath();
      ctx.arc(cx, cy, r, a0, a0 + step - gap);
      ctx.stroke();
    }
    ctx.restore();
  }

  function draw() {
    const [color, want] = PALETTE[S.state] || PALETTE.standby;
    targetEnergy = want;
    energy += (targetEnergy - energy) * 0.055;
    flash *= 0.93;
    t += 0.008 + energy * 0.028;

    const w = 260, cx = w / 2, cy = w / 2;
    ctx.clearRect(0, 0, w, w);
    ctx.shadowBlur = 14 + energy * 26;
    ctx.shadowColor = color;

    // outer tick ring
    ring(cx, cy, 118, 60, 0.055, -t * 0.35, color, 0.16 + energy * 0.14, 1);
    // segmented rings, counter-rotating
    ring(cx, cy, 104, 6,  0.30, t * 0.7,  color, 0.30 + energy * 0.4, 2.4);
    ring(cx, cy,  90, 12, 0.18, -t * 1.1, color, 0.22 + energy * 0.35, 1.6);
    ring(cx, cy,  74, 3,  0.55, t * 1.6,  color, 0.42 + energy * 0.5, 3.2);
    ring(cx, cy,  60, 24, 0.10, -t * 2.2, color, 0.20 + energy * 0.3, 1.2);

    // energy arc — how hard it's working
    ctx.save();
    ctx.strokeStyle = color; ctx.lineWidth = 3.4; ctx.lineCap = 'round';
    ctx.globalAlpha = 0.75;
    ctx.beginPath();
    ctx.arc(cx, cy, 46, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(energy, 1));
    ctx.stroke();
    ctx.restore();

    // triangular core (the reactor proper)
    const pulse = 1 + Math.sin(t * 3.1) * 0.07 * (0.4 + energy);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(t * 0.5);
    ctx.globalAlpha = 0.5 + energy * 0.45;
    ctx.strokeStyle = color; ctx.lineWidth = 2;
    ctx.beginPath();
    for (let i = 0; i < 3; i++) {
      const a = (i / 3) * Math.PI * 2 - Math.PI / 2;
      const R = 26 * pulse;
      i ? ctx.lineTo(Math.cos(a) * R, Math.sin(a) * R)
        : ctx.moveTo(Math.cos(a) * R, Math.sin(a) * R);
    }
    ctx.closePath(); ctx.stroke();
    ctx.restore();

    // glowing centre
    const R = 17 * pulse;
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 2.4);
    grad.addColorStop(0, color);
    grad.addColorStop(0.35, hexA(color, 0.55 + energy * 0.35 + flash));
    grad.addColorStop(1, hexA(color, 0));
    ctx.fillStyle = grad;
    ctx.beginPath(); ctx.arc(cx, cy, R * 2.4, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.globalAlpha = 0.55 + energy * 0.4;
    ctx.beginPath(); ctx.arc(cx, cy, R * 0.42, 0, Math.PI * 2); ctx.fill();
    ctx.globalAlpha = 1;

    requestAnimationFrame(draw);
  }
  draw();

  return { ping: () => { flash = 0.5; } };
})();

function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${Math.max(0, Math.min(a, 1))})`;
}

function setState(next) {
  S.state = next;
  const labels = {
    standby: 'STANDBY', thinking: 'PROCESSING', executing: 'EXECUTING',
    awaiting: 'AWAITING AUTHORISATION', speaking: 'RESPONDING', error: 'FAULT',
  };
  $('#state-text').textContent = labels[next] || next.toUpperCase();
  $('#stop').classList.toggle('hidden', next === 'standby');
}

/* ══ RADIAL GAUGES ═════════════════════════════════════════ */
const gauges = {};
$$('[data-gauge]').forEach((cv) => {
  const ctx = cv.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  cv.width = 118 * dpr; cv.height = 118 * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  gauges[cv.dataset.gauge] = { ctx, value: 0, shown: 0 };
});

function drawGauges() {
  for (const g of Object.values(gauges)) {
    g.shown += (g.value - g.shown) * 0.12;
    const { ctx } = g, c = 59, v = g.shown / 100;
    const color = v > 0.88 ? '#ff4d5a' : v > 0.7 ? '#ffb648' : '#38e0ff';
    ctx.clearRect(0, 0, 118, 118);

    // track
    ctx.strokeStyle = 'rgba(56,224,255,.1)'; ctx.lineWidth = 6;
    ctx.beginPath(); ctx.arc(c, c, 46, 0.75 * Math.PI, 2.25 * Math.PI); ctx.stroke();

    // value
    ctx.strokeStyle = color; ctx.lineWidth = 6; ctx.lineCap = 'round';
    ctx.shadowBlur = 12; ctx.shadowColor = color;
    ctx.beginPath();
    ctx.arc(c, c, 46, 0.75 * Math.PI, 0.75 * Math.PI + 1.5 * Math.PI * v);
    ctx.stroke();
    ctx.shadowBlur = 0;

    // ticks
    ctx.strokeStyle = 'rgba(56,224,255,.22)'; ctx.lineWidth = 1;
    for (let i = 0; i <= 20; i++) {
      const a = 0.75 * Math.PI + (1.5 * Math.PI * i) / 20;
      const r0 = i % 5 === 0 ? 34 : 37;
      ctx.beginPath();
      ctx.moveTo(c + Math.cos(a) * r0, c + Math.sin(a) * r0);
      ctx.lineTo(c + Math.cos(a) * 40, c + Math.sin(a) * 40);
      ctx.stroke();
    }
  }
  requestAnimationFrame(drawGauges);
}
drawGauges();

/* ══ TELEMETRY ═════════════════════════════════════════════ */
let lastNet = null, lastNetAt = 0;

function applyTelemetry(d) {
  if (!d || !d.available) {
    $('#r-disk').textContent = 'psutil absent';
    return;
  }
  gauges.cpu.value = d.cpu ?? 0;
  gauges.mem.value = d.memory ?? 0;
  $('#g-cpu').textContent = Math.round(d.cpu ?? 0) + '%';
  $('#g-mem').textContent = Math.round(d.memory ?? 0) + '%';

  // per-core bars
  const box = $('#cores');
  const cores = d.cpu_cores || [];
  if (box.children.length !== cores.length) {
    box.innerHTML = cores.map(() => '<div class="core-bar"><i></i></div>').join('');
  }
  cores.forEach((v, i) => {
    const bar = box.children[i]?.firstElementChild;
    if (bar) { bar.style.height = Math.max(3, v) + '%'; bar.style.opacity = 0.45 + v / 180; }
  });

  setRead('#r-disk', Math.round(d.disk ?? 0) + '% used', d.disk);
  setRead('#r-ram', `${d.memory_used_gb} / ${d.memory_total_gb} GB`, d.memory);
  $('#r-proc').textContent = d.processes ?? '—';
  $('#r-uptime').textContent = (d.uptime_h ?? 0) + ' h';

  if (d.battery !== undefined) {
    $('#row-batt').classList.remove('hidden');
    setRead('#r-batt', `${d.battery}% ${d.charging ? '⚡ charging' : 'on battery'}`,
            100 - d.battery);
  }

  if (d.net_recv !== undefined) {
    const now = Date.now();
    if (lastNet !== null) {
      const dt = (now - lastNetAt) / 1000;
      const kbs = ((d.net_recv - lastNet) / 1024 / Math.max(dt, 0.1));
      $('#r-net').textContent = kbs > 1024
        ? (kbs / 1024).toFixed(1) + ' MB/s'
        : Math.max(0, kbs).toFixed(0) + ' KB/s';
    }
    lastNet = d.net_recv; lastNetAt = now;
  }
}

function setRead(sel, text, pct) {
  const el = $(sel);
  el.textContent = text;
  el.className = pct > 90 ? 'crit' : pct > 75 ? 'hot' : '';
}

/* ══ TRANSCRIPT ════════════════════════════════════════════ */
function addMessage(kind, who, text) {
  const wrap = document.createElement('div');
  wrap.className = `msg ${kind}`;
  wrap.innerHTML = `<div class="who">${who}</div><div class="body"></div>`;
  const body = wrap.querySelector('.body');
  body.innerHTML = render(text);
  $('#transcript').appendChild(wrap);
  scrollDown();
  return body;
}

function render(text) {
  let h = (text || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  h = h.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, l, c) => `<pre>${c.trim()}</pre>`);
  h = h.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  h = h.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  return h;
}

function scrollDown() {
  const t = $('#transcript');
  t.scrollTop = t.scrollHeight;
}

function addTrace(tool, args) {
  const el = document.createElement('div');
  el.className = 'trace';
  const preview = Object.entries(args || {})
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(' ').slice(0, 150);
  el.innerHTML = `▸ <b>${tool}</b> <span style="opacity:.55">${preview}</span>
                  <span class="res"></span>`;
  $('#transcript').appendChild(el);
  scrollDown();
  return el;
}

/* ══ ACTIVITY LOG ══════════════════════════════════════════ */
const activity = new Map();

function logStart(tool, args, verdict) {
  $('.act-empty')?.remove();
  const el = document.createElement('div');
  el.className = 'act running';
  el.innerHTML = `<div class="n"><span>${tool}</span><em>${verdict === 'confirm' ? 'AUTH' : 'RUN'}</em></div>
                  <div class="s">${escape_(JSON.stringify(args || {}).slice(0, 220))}</div>`;
  $('#activity').prepend(el);
  activity.set(tool, el);
  // flash the matching capability chip
  const chip = $(`.cap[data-tool="${tool}"]`);
  if (chip) { chip.classList.add('firing'); setTimeout(() => chip.classList.remove('firing'), 1400); }
  return el;
}

function logEnd(tool, ok, summary, ms) {
  const el = activity.get(tool);
  if (!el) return;
  activity.delete(tool);
  const denied = /denied|declined|refused/i.test(summary || '');
  el.className = 'act ' + (ok ? '' : denied ? 'denied' : 'fail');
  el.querySelector('.n em').textContent = ok ? `${ms ?? 0}ms` : (denied ? 'DENIED' : 'FAIL');
  el.querySelector('.s').textContent = summary || '';
}

const escape_ = (s) => (s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/* ══ TRANSPORT ════════════════════════════════════════════ */
function connect() {
  NET.onPush(handle);
}

function tx(obj) {
  NET.send(obj);
}

function setLink(up) {
  $('#link-state').className = 'link-state ' + (up ? 'on' : 'off');
  $('#link-state span').textContent = up
    ? (NET.native ? 'LINKED' : 'ONLINE')
    : 'OFFLINE';
  if (!up) setState('standby');
}

function handle(m) {
  switch (m.type) {
    case '__link':
      setLink(m.up);
      break;

    case 'online':
      $('#stat-model').textContent = m.model || '—';
      $('#stat-tools').textContent = m.tools ?? '—';
      $('#r-mode').textContent = m.security?.label || '—';
      break;

    case 'telemetry':
      applyTelemetry(m.data);
      break;

    case 'status':
      if (m.state === 'thinking') setState('thinking');
      else if (m.state === 'executing') setState('executing');
      else if (m.state === 'awaiting_approval') setState('awaiting');
      else if (m.state === 'idle') {
        if (!S.speaking) setState('standby');
        if (m.elapsed) $('#stat-latency').textContent = m.elapsed + 's';
      }
      break;

    case 'message_start':
      S.streamText = '';
      S.streaming = addMessage('jarvis', 'J.A.R.V.I.S.', '');
      S.streaming.innerHTML = '<span class="caret"></span>';
      break;

    case 'delta':
      S.streamText += m.text;
      if (S.streaming) {
        S.streaming.innerHTML = render(S.streamText) + '<span class="caret"></span>';
        scrollDown();
      }
      reactor.ping();
      break;

    case 'message_end':
      if (S.streaming) S.streaming.innerHTML = render(S.streamText);
      S.streaming = null;
      if (m.usage?.total) {
        S.tokens += m.usage.total;
        $('#stat-tokens').textContent = S.tokens.toLocaleString();
      }
      break;

    case 'tool_start':
      setState(m.verdict === 'confirm' ? 'awaiting' : 'executing');
      addTrace(m.tool, m.args);
      logStart(m.tool, m.args, m.verdict);
      break;

    case 'tool_end': {
      logEnd(m.tool, m.ok, m.summary, m.ms);
      const traces = $$('.trace');
      const last = traces[traces.length - 1];
      if (last) {
        last.classList.toggle('fail', !m.ok);
        last.querySelector('.res').textContent =
          (m.ok ? '✓ ' : '✕ ') + (m.summary || '').slice(0, 160);
      }
      break;
    }

    case 'confirm_request':
      showConfirm(m);
      break;

    case 'face_check': {
      const el = $('#faceid');
      el.className = 'faceid ' + m.state;
      el.textContent = {
        scanning: 'SCANNING…',
        match: `IDENTITY CONFIRMED${m.name ? ' — ' + m.name.toUpperCase() : ''}` +
               ` · ${m.confidence}%`,
        no_match: 'FACE NOT RECOGNISED',
        unavailable: 'CAMERA UNAVAILABLE',
      }[m.state] || '';
      // Show the sweep on the reactor while the camera is being read.
      $('#reactor').parentElement.classList.toggle('scanning', m.state === 'scanning');
      if (m.state !== 'scanning') {
        setTimeout(() => { if ($('#faceid').className.includes(m.state)) $('#faceid').textContent = ''; }, 6000);
      }
      break;
    }

    case 'confirm_timeout':
      hideConfirm();
      addMessage('system', 'SYSTEM', 'Authorisation request expired.');
      break;

    case 'speak':
      speak(m.text);
      break;

    case 'client_request':
      handleClientRequest(m);
      break;

    case 'notice':
      addMessage('system', 'SYSTEM', m.message);
      break;

    case 'model_changed':
      $('#stat-model').textContent = m.model || '—';
      break;

    case 'mode_changed':
      setMode(m.mode, false);
      addMessage('system', 'SYSTEM', `Authority level set to ${m.mode.toUpperCase()}.`);
      break;

    case 'error':
      setState('error');
      addMessage('error', 'FAULT', m.message);
      setTimeout(() => setState('standby'), 2200);
      break;
  }
}

/* ══ AUTHORISATION DIALOG ══════════════════════════════════ */
let pendingId = null;

function showConfirm(m) {
  pendingId = m.id;
  $('#d-reason').textContent = m.reason || 'This operation requires your authorisation.';
  $('#d-tool').textContent = m.tool;
  $('#d-risk').textContent = (m.risk || '').toUpperCase();
  $('#d-args').textContent = JSON.stringify(m.args || {}, null, 2);
  $('#d-remember').checked = false;
  $('#confirm').classList.remove('hidden');
  $('#d-deny').focus();
}

function hideConfirm() {
  $('#confirm').classList.add('hidden');
  pendingId = null;
}

function answer(approved) {
  if (!pendingId) return;
  tx({ type: 'confirm_response', id: pendingId, approved,
       remember: approved && $('#d-remember').checked });
  hideConfirm();
}

/* ══ CAPABILITY GRID ═══════════════════════════════════════ */
function renderCaps(caps) {
  const groups = {};
  caps.forEach((c) => (groups[c.category] ||= []).push(c));
  $('#cap-count').textContent = caps.length;
  $('#caps').innerHTML = Object.entries(groups).map(([cat, list]) => `
    <div class="cap-group">
      <h3>${cat.toUpperCase()}</h3>
      <div class="cap-list">
        ${list.map((c) => `<span class="cap risk-${c.risk}" data-tool="${c.name}"
             title="${escape_(c.description)}">${c.name}</span>`).join('')}
      </div>
    </div>`).join('');
}

/* ══ SENDING ═══════════════════════════════════════════════ */
function send(text) {
  if (!text.trim()) return;
  addMessage('user', 'YOU', text);
  tx({ type: 'user_message', text });
  S.turnStart = Date.now();
  setState('thinking');
}

function setMode(mode, notify = true) {
  $$('.mode-btn').forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
  $('#r-mode').textContent =
    { readonly: 'OBSERVE ONLY', guarded: 'GUARDED', open: 'FULL AUTONOMY' }[mode] || mode;
  if (notify) tx({ type: 'set_mode', mode });
}

/* ══ WIRING ════════════════════════════════════════════════ */
/* Grow the box with the text so a long instruction stays readable. */
function autoGrow() {
  const el = $('#input');
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 152) + 'px';
}

function submitInput() {
  const el = $('#input');
  const value = el.value;
  el.value = '';
  autoGrow();
  send(value);
}

$('#send').onclick = submitInput;
$('#input').addEventListener('input', () => {
  autoGrow();
  // Start typing and JARVIS stops talking — you would not keep speaking over
  // someone who had clearly moved on.
  if (S.speaking) stopSpeaking();
});
$('#input').addEventListener('keydown', (e) => {
  // Enter sends; Shift+Enter is a new line, as in every chat box.
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    submitInput();
  }
});
$('#mic').onclick = toggleMic;
$('#stop').onclick = () => {
  tx({ type: 'interrupt' });
  stopSpeaking();
  if (typeof discardSpeech === 'function') discardSpeech();
  setState('standby');
};
$('#d-allow').onclick = () => answer(true);
$('#d-deny').onclick = () => answer(false);
$('#btn-clear').onclick = () => {
  tx({ type: 'clear_history' });
  $('#transcript').innerHTML = '';
  $('#activity').innerHTML = '<div class="act-empty">No operations yet.</div>';
};
$('#btn-mute').onclick = (e) => {
  S.voiceOn = !S.voiceOn;
  e.target.textContent = `VOICE: ${S.voiceOn ? 'ON' : 'OFF'}`;
  if (!S.voiceOn) stopSpeaking();
};
$$('.mode-btn').forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (pendingId) answer(false);
    else {
      tx({ type: 'interrupt' });
      stopSpeaking();
      if (typeof discardSpeech === 'function') discardSpeech();
    }
  }
  if (e.ctrlKey && e.code === 'Space') { e.preventDefault(); toggleMic(); }
  if (e.key === 'Enter' && pendingId && e.ctrlKey) answer(true);
});

/* ══ NATIVE WINDOW SHELL ═══════════════════════════════════
   When hosted in the native window the page draws its own title bar and
   drives real window operations through the pywebview bridge. In a browser
   none of this exists and the block is inert. */
function initNativeShell() {
  S.native = new URLSearchParams(location.search).get('shell') === 'native';
  if (!S.native) return;

  document.body.classList.add('native');
  $('#titlebar').classList.remove('hidden');

  // The bridge is injected slightly after the document, so tolerate its absence.
  const api = () => window.pywebview?.api;

  $('#tb-min').onclick = () => api()?.window_minimize();
  $('#tb-close').onclick = () => api()?.window_close();
  $('#tb-max').onclick = async () => {
    const maximised = await api()?.window_toggle_maximize();
    $('#tb-max').classList.toggle('restored', !!maximised);
  };
  // Double-clicking the drag strip toggles maximise, as Windows apps do.
  $('.tb-drag').ondblclick = () => $('#tb-max').click();
}

/* ══ START ═════════════════════════════════════════════════ */
(async function start() {
  initNativeShell();
  try {
    S.boot = await NET.boot();
  } catch {
    S.boot = {};
  }

  if (S.boot.identity) {
    $('#expansion').textContent = S.boot.identity.expansion || '';
    document.title = S.boot.identity.name || 'J.A.R.V.I.S.';
  }
  if (S.boot.ui) {
    const root = document.documentElement.style;
    if (S.boot.ui.accent) root.setProperty('--accent', S.boot.ui.accent);
    if (S.boot.ui.warn)   root.setProperty('--warn', S.boot.ui.warn);
    if (S.boot.ui.alert)  root.setProperty('--alert', S.boot.ui.alert);
  }
  if (S.boot.voice) {
    S.voiceOn    = S.boot.voice.tts !== false;
    S.voiceRate  = S.boot.voice.rate ?? 1.04;
    S.voicePitch = S.boot.voice.pitch ?? 0.92;
    $('#btn-mute').textContent = `VOICE: ${S.voiceOn ? 'ON' : 'OFF'}`;
  }
  if (S.boot.capabilities) renderCaps(S.boot.capabilities);
  if (S.boot.security) setMode(S.boot.security.mode, false);
  if (S.boot.memory) {
    $('#r-mem-facts').textContent = `${S.boot.memory.facts} facts`;
    $('#r-turns').textContent = S.boot.memory.turns;
  }
  if (S.boot.model) $('#stat-model').textContent = S.boot.model;

  initSpeech();
  connect();
  await runBoot();

  if (!S.boot.api_key_present) {
    $('#setup').classList.remove('hidden');
    $('#setup-key').focus();
  } else {
    const sir = S.boot.identity?.address || 'Sir';
    const greet = `All systems online, ${sir}. ${S.boot.capabilities?.length || 0} capabilities at your disposal. How may I help?`;
    addMessage('jarvis', 'J.A.R.V.I.S.', greet);
    setTimeout(() => speak(greet), 400);
  }
})();
