/* MediaHotKey mini player — shares the same pywebview Api as the main window. */

const $ = (s) => document.getElementById(s);
function ready() {
  return !!(window.pywebview && window.pywebview.api &&
            typeof window.pywebview.api.poll === 'function');
}
const api = () => window.pywebview.api;

let cur = null;
let lastArt = '';
let volDragging = false;

function fmt(ms) {
  const s = Math.max(0, Math.floor((ms || 0) / 1000));
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

function render(np) {
  np = np || {};
  cur = np;
  $('t').textContent = np.title || '—';
  $('a').textContent = np.artist || (np.title ? '' : 'not playing');
  $('play').textContent = np.is_playing ? '❚❚' : '▶';
  if (np.volume != null && np.volume !== undefined) {
    $('vpct').textContent = np.volume + '%';
    if (!volDragging) $('vrange').value = np.volume;
  } else if (!volDragging) {
    $('vpct').textContent = '—';
  }
  const art = np.art_url || '';
  if (art !== lastArt) {
    lastArt = art;
    $('art').style.backgroundImage = art ? `url("${art}")` : '';
    $('art').classList.toggle('filled', !!art);
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
  $('cur').textContent = fmt(pos);
  $('dur').textContent = fmt(dur);
  $('fill').style.width = dur ? Math.min(100, pos / dur * 100) + '%' : '0';
}
setInterval(tick, 500);

async function poll() {
  try { render((await api().poll()).now_playing); } catch (e) { /* closing */ }
}

function wire() {
  $('prev').onclick = () => api().transport('prev');
  $('play').onclick = () => api().transport('playpause');
  $('next').onclick = () => api().transport('next');
  $('vdown').onclick = () => api().volume('down');
  $('vup').onclick = () => api().volume('up');
  $('vrange').oninput = () => { volDragging = true; $('vpct').textContent = $('vrange').value + '%'; };
  $('vrange').onchange = async () => {
    await api().set_volume(parseInt($('vrange').value, 10));
    setTimeout(() => { volDragging = false; }, 400);
  };
  $('add').onclick = () => api().add_to_playlist();
  $('like').onclick = () => api().like();
  $('x').onclick = () => api().close_mini();
}

function boot() {
  wire();
  setInterval(poll, 1000);
  poll();
}

(function waitReady() {
  if (ready()) return boot();
  let n = 0;
  const t = setInterval(() => {
    if (ready() || ++n > 100) { clearInterval(t); boot(); }
  }, 100);
  window.addEventListener('pywebviewready', () => { clearInterval(t); boot(); });
})();
