/* MediaHotKey UI logic — talks to the Python engine via window.pywebview.api. */

const TABS = ['Spotify', 'Discord', 'Hotkeys', 'General', 'Log'];
const HOTKEYS = [
  ['next', 'Next track'],
  ['prev', 'Previous track'],
  ['playpause', 'Play / Pause'],
  ['add', 'Add to playlist (Spotify)'],
  ['like', 'Like to library (Spotify)'],
  ['toggle_mode', 'Toggle Spotify / Media mode'],
];
const OPTS = [
  ['track_activity', 'Track now-playing across devices (Spotify mode)'],
  ['announce_pause_resume', 'Also post when you pause / resume the same track'],
  ['start_engine_on_launch', 'Start hotkeys automatically when the app opens'],
  ['start_minimized', 'Launch minimized to the system tray'],
  ['update_check_on_launch', 'Check GitHub for updates on launch'],
  ['auto_install_updates', 'Automatically install updates on launch'],
];

let cfg = null;          // working copy of the config
let activeTab = 'Spotify';
let running = false;
let mascotImage = '';    // user-chosen art (data URL)
let lastArt = '';        // currently shown art url
let updateBusy = false;  // an update install is in progress
let volDragging = false; // user is dragging the volume slider

const $ = (sel) => document.querySelector(sel);

// When opened inside the app, window.pywebview.api is the real Python bridge.
// When opened directly in a browser (preview), fall back to demo data so the
// design renders fully without a backend.
const DEMO = {
  config: {
    spotify: { client_id: '95352be1ea884c468c68e0c2c0103099', client_secret: 'demo-secret-value',
      redirect_uri: 'http://127.0.0.1:8888/callback' },
    discord: { webhook_url: 'https://discord.com/api/webhooks/EXAMPLE/preview-only' },
    hotkeys: { next: 'f9', prev: 'shift+f9', playpause: 'ctrl+f9', add: 'alt+f9',
      like: 'ctrl+alt+f9', toggle_mode: 'ctrl+shift+f9' },
    settings: { start_mode: 'spotify', media_app_hint: 'brave', poll_interval: 5,
      track_activity: true, announce_pause_resume: true, start_engine_on_launch: true,
      start_minimized: true },
    mascot: {},
  },
  caps: { keyboard: true, spotipy: true, media: true },
  running: true, mode: 'spotify',
  config_path: 'C:\\Users\\you\\AppData\\Roaming\\MediaHotKey\\config.json',
  now_playing: { title: 'TANK', artist: 'NMIXX', art_url: null,
    progress_ms: 102000, duration_ms: 185000, is_playing: true, source: 'spotify', volume: 65 },
  logs: [
    '[ok] ♪  Now Playing: Kep1er — WA DA DA (Japanese ver.)',
    '[ok] ⇄  Switched to SPOTIFY mode',
    '[ok] ♪  Now Playing: NMIXX — TANK',
    '[ok] ＋  Added to playlist: NMIXX — TANK',
    '[ok] ♥  Liked to library: NMIXX — TANK',
    '[i] minimized to tray — hotkeys still active.',
    '[ok] Hotkeys active. Engine running.',
  ],
};
const MOCK = {
  get_state: async () => DEMO,
  poll: async () => ({ running: DEMO.running, mode: DEMO.mode, caps: DEMO.caps,
    now_playing: DEMO.now_playing, logs: DEMO.logs }),
  save_config: async () => ({ ok: true }),
  toggle_engine: async () => { DEMO.running = !DEMO.running; return { ok: true }; },
  transport: async () => ({ ok: true }),
  volume: async () => ({ ok: true }),
  set_volume: async () => ({ ok: true }),
  add_to_playlist: async () => ({ ok: true }),
  like: async () => ({ ok: true }),
  poll_np: async () => ({ now_playing: DEMO.now_playing }),
  open_mini: async () => ({ ok: true }),
  close_mini: async () => ({ ok: true }),
  open_bar: async () => ({ ok: true }),
  close_bar: async () => ({ ok: true }),
  test_spotify: async () => ({ ok: true, msg: 'Connected to Spotify (nothing playing right now)' }),
  test_discord: async () => ({ ok: true, msg: 'Test message sent. Check your channel.' }),
  record_hotkey: async () => '', open_url: () => {}, choose_mascot: async () => '',
  clear_log: async () => { DEMO.logs = []; return { ok: true }; },
  get_logs: async () => ({ logs: DEMO.logs }),
  get_changelog: async () => [
    { version: '1.0.5', notes: ['Now-playing cover shows the full art, no cropping.',
      'Added this patch-notes dropdown.'] },
    { version: '1.0.4', notes: ['Add to playlist / Like buttons in now-playing.'] },
  ],
  create_shortcut: async () => ({ ok: true, msg: 'Desktop shortcut created (demo).' }),
  minimize: () => {}, toggle_maximize: () => {}, close: () => {},
};
// The pywebview bridge creates window.pywebview.api as an empty object first,
// then attaches the Python methods a moment later. Only treat it as "real"
// once an actual method is present; otherwise use the demo MOCK.
function bridgeReady() {
  return !!(window.pywebview && window.pywebview.api &&
            typeof window.pywebview.api.get_state === 'function');
}
const api = () => (bridgeReady() ? window.pywebview.api : MOCK);

// ---------- rendering ----------
function renderTabs() {
  const host = $('#tabs');
  host.innerHTML = '';
  TABS.forEach((name) => {
    const el = document.createElement('div');
    el.className = 'tab' + (name === activeTab ? ' active' : '');
    el.textContent = name;
    el.onclick = () => {
      activeTab = name; renderTabs(); renderPanels();
      if (name === 'Log') api().get_logs().then((lg) => renderLog(lg.logs)).catch(() => {});
    };
    host.appendChild(el);
  });
}

function renderPanels() {
  document.querySelectorAll('.panel').forEach((p) => {
    p.classList.toggle('active', p.dataset.tab === activeTab);
  });
}

function renderHotkeys() {
  const host = $('#hotkeys');
  host.innerHTML = '';
  HOTKEYS.forEach(([key, label]) => {
    const row = document.createElement('div');
    row.className = 'hkrow';
    const lbl = document.createElement('div');
    lbl.className = 'lbl'; lbl.textContent = label;
    const inp = document.createElement('input');
    inp.className = 'field'; inp.value = cfg.hotkeys[key] || '';
    inp.oninput = () => { cfg.hotkeys[key] = inp.value.trim(); };
    const rec = document.createElement('button');
    rec.className = 'btn record'; rec.textContent = 'Record';
    rec.onclick = async () => {
      rec.classList.add('recording'); rec.textContent = 'press keys…';
      try {
        const combo = await api().record_hotkey();
        if (combo) { inp.value = combo; cfg.hotkeys[key] = combo; }
      } catch (e) { /* ignore */ }
      rec.classList.remove('recording'); rec.textContent = 'Record';
    };
    row.append(lbl, inp, rec);
    host.appendChild(row);
  });
}

function renderOpts() {
  const host = $('#opts');
  host.innerHTML = '';
  OPTS.forEach(([key, label]) => {
    const row = document.createElement('div');
    row.className = 'opt-row';
    const tg = document.createElement('div');
    tg.className = 'toggle' + (cfg.settings[key] ? ' on' : '');
    tg.innerHTML = '<div class="knob"></div>';
    const span = document.createElement('span');
    span.textContent = label;
    row.onclick = () => {
      cfg.settings[key] = !cfg.settings[key];
      tg.classList.toggle('on', cfg.settings[key]);
    };
    row.append(tg, span);
    host.appendChild(row);
  });
}

function renderSeg() {
  document.querySelectorAll('#seg-mode .opt').forEach((o) => {
    o.classList.toggle('active', o.dataset.val === cfg.settings.start_mode);
    o.onclick = () => {
      cfg.settings.start_mode = o.dataset.val;
      renderSeg();
    };
  });
}

function fillFields() {
  $('#f-client-id').value = cfg.spotify.client_id || '';
  $('#f-client-secret').value = cfg.spotify.client_secret || '';
  $('#f-redirect').value = cfg.spotify.redirect_uri || '';
  $('#f-webhook').value = cfg.discord.webhook_url || '';
  $('#f-hint').value = cfg.settings.media_app_hint || '';
  $('#f-poll').value = cfg.settings.poll_interval ?? 5;
}

function bindFields() {
  $('#f-client-id').oninput = (e) => cfg.spotify.client_id = e.target.value.trim();
  $('#f-client-secret').oninput = (e) => cfg.spotify.client_secret = e.target.value.trim();
  $('#f-redirect').oninput = (e) => cfg.spotify.redirect_uri = e.target.value.trim();
  $('#f-webhook').oninput = (e) => cfg.discord.webhook_url = e.target.value.trim();
  $('#f-hint').oninput = (e) => cfg.settings.media_app_hint = e.target.value.trim();
  $('#f-poll').oninput = (e) => cfg.settings.poll_interval = e.target.value;
}

// ---------- now-playing ----------
function fmt(ms) {
  const s = Math.max(0, Math.floor((ms || 0) / 1000));
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

function setArt(url) {
  const art = $('#art');
  const show = url || mascotImage || '';
  if (show !== lastArt) {
    lastArt = show;
    art.style.backgroundImage = show ? `url("${show}")` : '';
    art.classList.toggle('filled', !!show);
  }
}

let curNP = null;        // last now-playing payload from the backend
function renderNowPlaying(np) {
  np = np || {};
  curNP = np;
  $('#np-title').textContent = np.title || '—';
  $('#np-artist').textContent = np.artist || (np.title ? '' : 'not playing');
  $('#tp-play').textContent = np.is_playing ? '❚❚' : '▶';
  setArt(np.art_url);
  if (np.volume != null && np.volume !== undefined) {
    $('#vol-pct').textContent = np.volume + '%';
    if (!volDragging) $('#vol-range').value = np.volume;
  } else if (!volDragging) {
    $('#vol-pct').textContent = '—';
  }
  tickProgress();
}

// Advance the progress bar smoothly between backend updates by extrapolating
// from the fetch timestamp while the track is playing.
function tickProgress() {
  const np = curNP;
  if (!np) return;
  const dur = np.duration_ms || 0;
  let cur = np.progress_ms || 0;
  if (np.is_playing && np.fetched_at) cur += Date.now() - np.fetched_at;
  if (dur) cur = Math.min(cur, dur);
  $('#np-cur').textContent = fmt(cur);
  $('#np-dur').textContent = fmt(dur);
  $('#np-fill').style.width = dur ? Math.min(100, cur / dur * 100) + '%' : '0';
}
setInterval(tickProgress, 500);

// ---------- status / caps ----------
function renderCaps(caps) {
  const cell = (label, ok) =>
    `<span>${label} <span class="${ok ? 'ok' : 'no'}">${ok ? '✓' : '✗'}</span></span>`;
  $('#caps').innerHTML =
    cell('keyboard', caps.keyboard) + cell('spotify', caps.spotipy) +
    cell('media/SMTC', caps.media);
}

function renderRunning(isRunning, mode) {
  running = isRunning;
  const pill = $('#run-pill');
  pill.className = 'pill ' + (isRunning ? 'running' : 'stopped');
  $('#run-txt').textContent = isRunning ? 'Running' : 'Stopped';
  $('#mode-val').textContent = (mode || cfg.settings.start_mode || 'spotify').toUpperCase();
  const btn = $('#btn-engine');
  if (isRunning) { btn.className = 'btn foot stop'; btn.textContent = '■ Stop hotkeys'; }
  else { btn.className = 'btn foot'; btn.style.background =
    'linear-gradient(135deg,#87A06F,#6E8C56)'; btn.style.color = '#fff';
    btn.textContent = '▶ Start hotkeys'; }
}

let lastLogSig = '';
function renderLog(lines) {
  lines = lines || [];
  const sig = lines.length + '|' + (lines[lines.length - 1] || '');
  if (sig === lastLogSig) return;     // nothing changed — skip the rebuild
  lastLogSig = sig;
  const c = $('#console');
  const atBottom = c.scrollTop + c.clientHeight >= c.scrollHeight - 20;
  c.innerHTML = lines.map((l) => {
    let pre = '', body = l;
    const m = /^(\[[a-z!]+\]|\S+)\s+(.*)$/.exec(l);
    if (l.startsWith('[')) { const i = l.indexOf(']'); pre = l.slice(0, i + 1); body = l.slice(i + 1).trim(); }
    return `<div class="logline"><span class="pre">${esc(pre)}</span><span class="bd">${esc(body)}</span></div>`;
  }).join('');
  if (atBottom) c.scrollTop = c.scrollHeight;
}

function esc(s) { return (s || '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }

// ---------- toast ----------
let toastT = null;
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('show'), 1600);
}

// ---------- actions ----------
function wire() {
  $('#btn-save').onclick = async () => { await api().save_config(cfg); toast('Settings saved ✓'); };
  $('#btn-engine').onclick = async () => {
    const res = await api().toggle_engine(cfg);
    if (res && res.error) toast(res.error);
  };
  $('#btn-dashboard').onclick = () => api().open_url('https://developer.spotify.com/dashboard');
  $('#btn-test-spotify').onclick = async () => {
    const s = $('#spotify-status'); s.className = 'status'; s.textContent = 'Authorizing… a browser tab may open.';
    const r = await api().test_spotify(cfg);
    s.className = 'status' + (r.ok ? '' : ' err');
    s.innerHTML = r.ok ? `✓ ${esc(r.msg)}` : `✗ ${esc(r.msg)}`;
  };
  $('#btn-test-discord').onclick = async () => {
    const s = $('#discord-status'); const r = await api().test_discord(cfg);
    s.className = 'status' + (r.ok ? '' : ' err');
    s.textContent = (r.ok ? '✓ ' : '✗ ') + r.msg;
  };
  $('#btn-clear').onclick = async () => { await api().clear_log(); renderLog([]); };
  $('#art').onclick = async () => {
    const data = await api().choose_mascot();
    if (data) { mascotImage = data; cfg.mascot = { image: data }; setArt(null); }
  };
  $('#tp-prev').onclick = () => api().transport('prev', cfg);
  $('#tp-next').onclick = () => api().transport('next', cfg);
  $('#tp-play').onclick = () => api().transport('playpause', cfg);
  $('#np-add').onclick = () => { api().add_to_playlist(); toast('Adding to playlist…'); };
  $('#np-like').onclick = () => { api().like(); toast('Saving to Liked Songs…'); };
  $('#vol-down').onclick = () => api().volume('down');
  $('#vol-up').onclick = () => api().volume('up');
  $('#vol-range').oninput = () => {
    volDragging = true;
    $('#vol-pct').textContent = $('#vol-range').value + '%';
  };
  $('#vol-range').onchange = async () => {
    await api().set_volume(parseInt($('#vol-range').value, 10));
    setTimeout(() => { volDragging = false; }, 400);
  };
  $('#btn-mini').onclick = async () => {
    const r = await api().open_mini();
    toast(r && r.ok === false ? (r.msg || 'Mini player failed') : 'Mini player opened');
  };
  $('#btn-bar').onclick = async () => {
    const r = await api().open_bar();
    toast(r && r.ok === false ? (r.msg || 'Taskbar bar failed')
                              : 'Taskbar bar opened — drag it over your taskbar');
  };

  $('#btn-check-update').onclick = async () => {
    $('#upd-status').textContent = 'checking…';
    renderUpdate(await api().check_update());
  };
  $('#btn-do-update').onclick = async () => {
    updateBusy = true;
    $('#upd-status').textContent = 'downloading & installing…';
    const r = await api().apply_update();
    updateBusy = false;
    if (r.ok) {
      $('#upd-status').textContent = 'installed ✓ — restart to apply';
      $('#btn-restart').style.display = '';
    } else {
      $('#upd-status').textContent = r.msg || 'update failed';
    }
  };
  $('#btn-restart').onclick = () => api().relaunch();
  $('#btn-shortcut').onclick = async () => {
    const r = await api().create_shortcut();
    toast(r && r.msg ? r.msg : 'Done');
  };

  const dpt = $('#discord-pause-toggle');
  dpt.classList.toggle('on', !!(cfg.discord && cfg.discord.paused));
  $('#discord-pause-row').onclick = async () => {
    cfg.discord.paused = !cfg.discord.paused;
    dpt.classList.toggle('on', cfg.discord.paused);
    await api().save_config(cfg);   // apply immediately
    toast(cfg.discord.paused ? 'Webhooks paused' : 'Webhooks resumed');
  };
}

async function loadPatchNotes() {
  let data = [];
  try { data = await api().get_changelog(); } catch (e) { /* ignore */ }
  const host = $('#patch-list');
  if (!host) return;
  host.innerHTML = '';
  (data || []).forEach((v) => {
    const block = document.createElement('div');
    block.className = 'patch-ver';
    const h = document.createElement('div');
    h.className = 'patch-h';
    h.textContent = 'v' + v.version + (v.date ? ' · ' + v.date : '');
    const ul = document.createElement('ul');
    (v.notes || []).forEach((n) => {
      const li = document.createElement('li');
      li.textContent = n;
      ul.appendChild(li);
    });
    block.append(h, ul);
    host.appendChild(block);
  });
}

function renderUpdate(info) {
  if (!info || updateBusy) return;
  if (info.version) $('#upd-version').textContent = info.version;
  const st = $('#upd-status'), restart = $('#btn-restart');
  if (info.installed) {
    st.textContent = 'installed ✓ — restart to apply';
    restart.style.display = '';
  } else if (info.available) {
    st.textContent = 'a new version is available — click Update now';
  } else if (info.error) {
    st.textContent = "couldn't check (you can still click Update now)";
  } else if (info.remote || info.current || info.first_run !== undefined) {
    st.textContent = 'up to date';
  }
}

// ---------- boot ----------
async function boot() {
  let state;
  if (bridgeReady()) {
    // Real app: ONLY ever use the live config. Never fall back to demo data
    // here — that would load the fake/truncated webhook and a later save would
    // overwrite the real one.
    try {
      state = await api().get_state();
    } catch (e) {
      showError(e);
      booted = false;                 // allow a retry
      setTimeout(go, 800);
      return;
    }
  } else {
    state = await MOCK.get_state();   // browser preview only (no pywebview)
  }
  cfg = state.config || {};
  cfg.spotify = cfg.spotify || {}; cfg.discord = cfg.discord || {};
  cfg.hotkeys = cfg.hotkeys || {}; cfg.settings = cfg.settings || {};
  if (!cfg.mascot) cfg.mascot = {};
  mascotImage = cfg.mascot.image || '';
  $('#cfgpath').textContent = 'Config file: ' + (state.config_path || '');
  $('#upd-version').textContent = state.version || '';

  renderTabs(); renderPanels(); renderHotkeys(); renderOpts(); renderSeg();
  fillFields(); bindFields(); wire(); loadPatchNotes();
  renderCaps(state.caps || {});
  renderRunning(state.running, state.mode);
  setArt(null);

  setInterval(poll, 1000);
  poll();
}

async function poll() {
  try {
    const p = await api().poll();
    renderRunning(p.running, p.mode);
    renderCaps(p.caps);
    // now_playing is null when unchanged since the last poll — keep the current
    // one (tickProgress keeps the bar moving) instead of re-rendering.
    if (p.now_playing) renderNowPlaying(p.now_playing);
    if (p.update) renderUpdate(p.update);
    if (activeTab === 'Log') {           // logs are heavy — only when visible
      const lg = await api().get_logs();
      renderLog(lg.logs);
    }
  } catch (e) { /* window closing */ }
}

let booted = false;
function go() {
  if (booted) return;
  booted = true;
  Promise.resolve().then(boot).catch(showError);
}

// Surface any failure on-screen instead of leaving a blank window.
function showError(err) {
  const msg = (err && (err.stack || err.message)) || String(err);
  let bar = document.getElementById('errbar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'errbar';
    bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99;' +
      'background:#C76A45;color:#fff;font:12px/1.4 monospace;padding:8px 12px;' +
      'white-space:pre-wrap;max-height:40vh;overflow:auto';
    document.body.appendChild(bar);
  }
  bar.textContent = 'UI error — please screenshot this:\n' + msg;
}
window.addEventListener('error', (e) => showError(e.error || e.message));

// Wait until the pywebview bridge has actually attached its methods (not just
// the empty placeholder object).
//  - In a plain browser (no window.pywebview at all) → use demo data after ~1.5s.
//  - Inside the app (window.pywebview exists) → wait as long as it takes for the
//    bridge methods. We NEVER fall back to demo data in the app, because booting
//    on demo data would load the fake webhook and a later save would overwrite
//    the real config.
function waitForBridge(cb) {
  if (bridgeReady()) return cb();
  let tries = 0;
  const timer = setInterval(() => {
    tries++;
    if (bridgeReady()) { clearInterval(timer); return cb(); }
    if (!window.pywebview && tries > 7) { clearInterval(timer); return cb(); } // browser preview only
  }, 200);
  window.addEventListener('pywebviewready', () => {
    const t2 = setInterval(() => {
      if (bridgeReady()) { clearInterval(t2); clearInterval(timer); cb(); }
    }, 50);
    setTimeout(() => clearInterval(t2), 10000);
  });
}

window.addEventListener('DOMContentLoaded', () => waitForBridge(go));
if (document.readyState !== 'loading') waitForBridge(go);
