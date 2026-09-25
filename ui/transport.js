/* ══════════════════════════════════════════════════════════
   J.A.R.V.I.S. — transport

   The HUD never talks to the network directly. It calls NET, which
   routes to one of two backends:

     bridge  the native window. Calls go straight into Python through
             window.pywebview.api; events arrive by Python evaluating
             window.__jarvisPush. No socket, no port, no origin.

     http    the browser fallback. Ordinary fetch + WebSocket against
             the local server.

   Both expose the identical surface, so nothing above this file knows
   or cares which one is in use.
   ══════════════════════════════════════════════════════════ */
'use strict';

const NET = (() => {
  const isNative = new URLSearchParams(location.search).get('shell') === 'native'
                || location.hostname === 'jarvis.local';

  let pushHandler = null;
  let socket = null;
  let retry = 0;

  /* Python calls this to deliver an event. Same shape as a WebSocket frame. */
  window.__jarvisPush = (payload) => {
    try {
      pushHandler?.(payload);
    } catch (err) {
      console.error('push handler failed', err, payload);
    }
  };

  /* ── native bridge ─────────────────────────────────────── */
  const bridgeReady = isNative
    ? new Promise((resolve) => {
        if (window.pywebview?.api) return resolve();
        window.addEventListener('pywebviewready', () => resolve(), { once: true });
        // Belt and braces: the event can fire before this listener attaches.
        const poll = setInterval(() => {
          if (window.pywebview?.api) { clearInterval(poll); resolve(); }
        }, 60);
        setTimeout(() => { clearInterval(poll); resolve(); }, 15000);
      })
    : Promise.resolve();

  async function callBridge(method, ...args) {
    await bridgeReady;
    const api = window.pywebview?.api;
    if (!api?.[method]) throw new Error(`bridge method unavailable: ${method}`);
    return api[method](...args);
  }

  /* ── http fallback ─────────────────────────────────────── */
  const getJSON = (path) => fetch(path).then((r) => r.json());
  const postJSON = (path, body) =>
    fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    }).then((r) => r.json());

  function openSocket() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    socket = new WebSocket(`${proto}://${location.host}/ws`);
    socket.onopen = () => { retry = 0; pushHandler?.({ type: '__link', up: true }); };
    socket.onclose = () => {
      pushHandler?.({ type: '__link', up: false });
      retry += 1;
      setTimeout(openSocket, Math.min(1000 * retry, 8000));
    };
    socket.onmessage = (e) => {
      try { pushHandler?.(JSON.parse(e.data)); } catch {}
    };
  }

  /* ── the surface ───────────────────────────────────────── */
  return {
    native: isNative,
    mode: isNative ? 'bridge' : 'http',

    onPush(handler) {
      pushHandler = handler;
      if (isNative) {
        // The bridge has no connection to lose; report up once it exists.
        bridgeReady.then(() => handler({ type: '__link', up: true }));
      } else {
        openSocket();
      }
    },

    send(message) {
      if (isNative) {
        callBridge('send', message).catch((err) =>
          console.error('send failed', message?.type, err));
      } else if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(message));
      }
    },

    boot()          { return isNative ? callBridge('boot')        : getJSON('/api/boot'); },
    history()       { return isNative ? callBridge('history')     : getJSON('/api/history'); },
    settings()      { return isNative ? callBridge('get_settings'): getJSON('/api/settings'); },
    faceStatus()    { return isNative ? callBridge('face')        : getJSON('/api/face'); },
    voices()        { return isNative ? callBridge('voices')      : getJSON('/api/voices'); },

    saveSettings(patch) {
      return isNative ? callBridge('save_settings', patch)
                      : postJSON('/api/settings', patch);
    },

    enrolFace(name, samples) {
      return isNative ? callBridge('enrol_face', name, samples)
                      : postJSON('/api/enrol-face', { name, samples });
    },

    forgetFace() {
      return isNative ? callBridge('forget_face') : postJSON('/api/forget-face', {});
    },

    testKey(key) {
      return isNative ? callBridge('test_key', key)
                      : postJSON('/api/test-key', { key });
    },

    /* Returns a playable object URL, or throws with a readable reason. */
    async speech(text) {
      if (isNative) {
        const res = await callBridge('tts', text);
        if (!res?.ok) throw new Error(res?.error || 'speech synthesis failed');
        // Base64 over the bridge; rebuild the bytes on this side.
        const bytes = Uint8Array.from(atob(res.b64), (c) => c.charCodeAt(0));
        return URL.createObjectURL(new Blob([bytes], { type: res.mime || 'audio/mpeg' }));
      }
      const res = await fetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      }
      return URL.createObjectURL(await res.blob());
    },
  };
})();
