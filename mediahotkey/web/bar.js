/* MediaHotKey taskbar bar — a tiny horizontal strip meant to hover over the
   Windows taskbar while gaming in windowed mode.
   IMPORTANT: this window must NOT poll the Python bridge on a timer. Two
   pywebview windows both calling js_api every second saturates/deadlocks the
   shared GUI-thread bridge and freezes the whole app. The main app PUSHES
   now-playing into window.mhkRender() via evaluate_js; we only call js_api for
   the (rare, user-initiated) button clicks. */

const $ = (s) => document.getElementById(s);
function ready() {
  return !!(window.pywebview && window.pywebview.api);
}
const api = () => window.pywebview.api;

let cur = null, lastArt = '';

function render(np) {
  np = np || {};
  cur = np;
  $('t').textContent = np.title || '—';
  $('a').textContent = np.artist || (np.title ? '' : 'not playing');
  $('play').textContent = np.is_playing ? '❚❚' : '▶';
  const art = np.art_url || '';
  if (art !== lastArt) {
    lastArt = art;
    $('art').style.backgroundImage = art ? `url("${art}")` : '';
  }
  tick();
}

function tick() {
  const np = cur;
  if (!np) return;
  const dur = np.duration_ms || 0;
  let pos = np.progress_ms || 0;
  if (np.is_playing && np.fetched_at) pos += Date.now() - np.fetched_at;
  if (dur) pos = Math.min(pos, dur);
  $('fill').style.width = dur ? Math.min(100, pos / dur * 100) + '%' : '0';
}
setInterval(tick, 500);

// The main app pushes now-playing here (Python -> JS via evaluate_js). Exposed
// immediately so pushes land even before the bridge finishes attaching.
window.mhkRender = render;

function wire() {
  $('prev').onclick = () => api().transport('prev');
  $('play').onclick = () => api().transport('playpause');
  $('next').onclick = () => api().transport('next');
  $('x').onclick = () => api().close_bar();
}

function boot() { wire(); }

(function waitReady() {
  if (ready()) return boot();
  let n = 0;
  const t = setInterval(() => {
    if (ready() || ++n > 100) { clearInterval(t); boot(); }
  }, 100);
  window.addEventListener('pywebviewready', () => { clearInterval(t); boot(); });
})();
