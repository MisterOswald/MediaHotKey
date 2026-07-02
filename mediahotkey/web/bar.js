/* MediaHotKey taskbar bar — a tiny horizontal strip meant to hover over the
   Windows taskbar while gaming in windowed mode. Shares the main Api bridge
   and the lightweight arg-less poll_np() feed. */

const $ = (s) => document.getElementById(s);
function ready() {
  return !!(window.pywebview && window.pywebview.api &&
            typeof window.pywebview.api.poll_np === 'function');
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

async function poll() {
  // now_playing is null when unchanged — keep the current one so the big cover
  // payload isn't re-sent every second.
  try {
    const np = (await api().poll_np()).now_playing;
    if (np) render(np);
  } catch (e) { /* closing */ }
}

function wire() {
  $('prev').onclick = () => api().transport('prev');
  $('play').onclick = () => api().transport('playpause');
  $('next').onclick = () => api().transport('next');
  $('x').onclick = () => api().close_bar();
}

function boot() { wire(); setInterval(poll, 1000); poll(); }

(function waitReady() {
  if (ready()) return boot();
  let n = 0;
  const t = setInterval(() => {
    if (ready() || ++n > 100) { clearInterval(t); boot(); }
  }, 100);
  window.addEventListener('pywebviewready', () => { clearInterval(t); boot(); });
})();
