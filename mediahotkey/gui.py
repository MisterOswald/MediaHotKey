"""
MediaHotKey desktop app — "Lo-fi Café" UI.

The interface is HTML/CSS/JS (see mediahotkey/web/) rendered in a native
window via pywebview; this module is the Python side: it serves the window,
bridges JavaScript ↔ the Engine through the `Api` class, and streams the log
and now-playing state back to the UI.
"""

import os
import sys
import time
import json
import base64
import threading
import collections

# Startup timing: stamp before/after the heavy imports below so the Log tab can
# show where launch time actually goes ("[start] …").
_T_START = time.time()

try:
    import webview
except Exception:  # noqa: BLE001
    webview = None

from . import __version__, updater
from .changelog import CHANGELOG
from .config import load_config, save_config, config_path, token_cache_path, config_dir
from .engine import (Engine, KEYBOARD_AVAILABLE, MEDIA_AVAILABLE,
                     SpotifyNotAuthorized)
from .discord_notify import Discord

if KEYBOARD_AVAILABLE:
    import keyboard

_T_IMPORTS = time.time()

# Optional system-tray support (keeps hotkeys alive after closing the window).
# pystray pulls in PIL, which is slow enough to matter at launch — load it
# lazily on first use (first hide-to-tray / minimized start) instead.
_TRAY_MODS = None  # None = not tried; {} = tried and unavailable; dict = loaded


def _tray_mods():
    global _TRAY_MODS
    if _TRAY_MODS is None:
        try:
            import pystray
            from PIL import Image
            _TRAY_MODS = {"pystray": pystray, "Image": Image}
        except Exception:  # noqa: BLE001
            _TRAY_MODS = {}
    return _TRAY_MODS or None


def _resource_dir():
    """Folder holding the web assets — works in source and PyInstaller builds."""
    if getattr(sys, "_MEIPASS", None):
        return os.path.join(sys._MEIPASS, "mediahotkey", "web")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def _icon_path():
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, "assets", "icon.ico")
    return p if os.path.exists(p) else None


def _check_spotify_creds(cid, secret):
    """Validate the Client ID/Secret pair directly against Spotify's token
    endpoint (client-credentials grant) BEFORE opening a browser sign-in, so a
    wrong / regenerated pair fails in a second with a clear message instead of
    an error page in a browser tab the user may never see. Returns an error
    string, or None when the pair is fine (or the check itself couldn't run)."""
    import urllib.error
    import urllib.parse
    import urllib.request
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
        headers={"Authorization": "Basic " + basic,
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=10):
            return None
    except urllib.error.HTTPError as e:
        if e.code in (400, 401):
            return ("Spotify rejected this Client ID / Secret pair. Re-copy "
                    "BOTH from developer.spotify.com → your app → Settings — "
                    "and note that regenerating the secret kills the old one.")
        return None
    except Exception:  # noqa: BLE001 — network hiccup; don't block the sign-in
        return None


def _wait_for_auth_code(port, timeout=120):
    """Serve 127.0.0.1:<port> until Spotify's approval redirect arrives (or the
    timeout passes). Returns (code, error): code on success, error string when
    Spotify redirected back with ?error=..., (None, None) on timeout. Handles
    stray requests (favicon etc.) without eating the real redirect."""
    import http.server
    import urllib.parse
    box = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = (q.get("code") or [None])[0]
            error = (q.get("error") or [None])[0]
            if code or error:
                box["code"], box["error"] = code, error
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                msg = ("✅ Signed in — you can close this tab and go back to "
                       "MediaHotKey." if code else
                       f"✗ Sign-in failed: {error}. Go back to MediaHotKey.")
                self.wfile.write(
                    f"<h2 style='font-family:sans-serif'>{msg}</h2>".encode())
            else:
                self.send_response(204)   # favicon & friends — keep waiting
                self.end_headers()

        def log_message(self, *_a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", int(port)), Handler)
    srv.timeout = 5
    deadline = time.time() + timeout
    try:
        while time.time() < deadline and "code" not in box:
            srv.handle_request()
    finally:
        srv.server_close()
    return box.get("code"), box.get("error")


def _port_in_use(port):
    """True if nothing can listen on 127.0.0.1:<port> (someone's bound to it)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", int(port)))
            return False
        except OSError:
            return True


def _merge(dst, src):
    """In-place deep merge of src into dst (keeps the same dict object)."""
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v
    return dst


class Api:
    """Everything the JavaScript front-end can call."""

    def __init__(self):
        self.config = load_config()
        self.logs = collections.deque(maxlen=400)
        self._log_lock = threading.Lock()
        # Every log line is also appended (timestamped) to a file in the
        # app's data folder, so sessions can be reviewed/exported after the
        # fact — crucial for diagnosing issues that build up over hours.
        self._log_path = os.path.join(config_dir(), "activity.log")
        self.window = None
        self.mini_window = None
        self.bar_window = None
        self._allow_close = False
        self._tray = None
        self.engine = Engine(self.config, log=self._log,
                             on_mode_change=lambda m: None)

        # Now-playing watcher (started AFTER the window loads — see
        # start_now_playing — so its COM/winsdk work doesn't contend with
        # WebView2's cold start and slow the launch).
        self._np_stop = threading.Event()
        self._np_last_spotify = None
        self._np_last_spotify_t = 0.0
        self._np_misses = 0
        self._np_art_by_track = {}   # {"title|artist": art_url} — stable cover
        self._np_last_sig = None
        self._np_last_sig_t = 0.0
        self._np_np_sig = None       # poll() de-dupe
        self._np_np_sig_t = 0.0
        self._overlay_sig = None     # _push_overlays() de-dupe
        self._overlay_sig_t = 0.0
        self._overlay_visible = {"mini": False, "bar": False}
        self._push_thread = None     # in-flight overlay push (never awaited)
        self._np_last_vol = None
        self._np_last_vol_t = 0.0
        self._np_thread = None
        self._update_info = {}       # last update-check result (for the UI)

        # Health telemetry (logged every 30s as a "[health]" line).
        self._last_poll_t = 0.0      # last JS poll() — detects a wedged window
        self._tick_n = 0             # now-playing tick timing stats
        self._tick_total = 0.0
        self._tick_max = 0.0

    def start_now_playing(self):
        if self._np_thread is None or not self._np_thread.is_alive():
            self._np_thread = threading.Thread(target=self._np_loop, daemon=True)
            self._np_thread.start()

    # -- logging ----------------------------------------------------------
    def _log(self, msg):
        msg = str(msg)
        with self._log_lock:
            self.logs.append(msg)
            try:
                # Rotate at ~2 MB (keep one previous file) so it can run for
                # months without growing unbounded.
                if (os.path.exists(self._log_path)
                        and os.path.getsize(self._log_path) > 2_000_000):
                    old = self._log_path + ".1"
                    if os.path.exists(old):
                        os.remove(old)
                    os.replace(self._log_path, old)
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                with open(self._log_path, "a", encoding="utf-8") as fh:
                    fh.write(f"{stamp} {msg}\n")
            except OSError:
                pass

    # -- now-playing watcher ---------------------------------------------
    def _np_loop(self):
        """Continuously refresh engine.now_playing. Windows SMTC is preferred
        (it sees the Spotify desktop app + browser, gives smooth local position,
        and — crucially — lets the panel transport control playback without
        Premium); the Spotify Web API is the fallback for remote-device
        playback.

        The loop must never die silently — any per-iteration failure (even a
        BaseException like a leaked CancelledError from an async read) is
        logged (throttled) and the loop carries on."""
        while not self._np_stop.is_set():
            t0 = time.perf_counter()
            try:
                self._np_tick()
            except BaseException as exc:  # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                now = time.time()
                if now - getattr(self, "_np_crash_t", 0) > 60:
                    self._np_crash_t = now
                    self._log(f"[!] now-playing watcher error (recovered): "
                              f"{type(exc).__name__}: {exc}")
            dt = time.perf_counter() - t0
            self._tick_n += 1
            self._tick_total += dt
            self._tick_max = max(self._tick_max, dt)
            if dt > 2.0:
                self._log(f"[health] slow now-playing tick: {dt:.1f}s — if "
                          "this coincides with a stutter, this thread is the "
                          "culprit")
            self._np_stop.wait(1.0)

    def _np_tick(self):
        """One refresh pass — the body of the watcher loop."""
        np = None
        now = time.time()
        spec = self.config.get("spotify", {})
        # Only hit the Web API if the user has authorized once (token cache
        # present) — never trigger an interactive login from here.
        can_spotify = bool(spec.get("client_id") and spec.get("client_secret")
                           and os.path.exists(token_cache_path()))

        if MEDIA_AVAILABLE:
            np = self.engine.read_media_now_playing()
        # When the active app is Spotify (or nothing local is playing) and
        # the Web API is authorized, use the Web API now-playing — its
        # volume is Spotify's OWN volume, so the slider stays in sync with
        # Spotify's UI both ways. (Throttled to ~3s.)
        is_spotify_app = bool(np and "spotify" in (np.get("app") or ""))
        if can_spotify and (np is None or is_spotify_app):
            if now - self._np_last_spotify_t >= 3:
                self._np_last_spotify_t = now
                self._np_last_spotify = self.engine.read_spotify_now_playing()
            if self._np_last_spotify:
                np = self._np_last_spotify

        if np:
            # Lock the cover art to the first one we get for this track so
            # it can't flip (SMTC data-URL vs Spotify https URL) or blink
            # out when one source momentarily lacks art. Keyed on the title
            # alone (normalized) so artist-string differences between the
            # two sources don't break the lock.
            key = (np.get("title") or "").strip().lower()
            if np.get("art_url"):
                if key not in self._np_art_by_track:
                    self._np_art_by_track = {key: np["art_url"]}
                np["art_url"] = self._np_art_by_track.get(key) or np["art_url"]
            elif key in self._np_art_by_track:
                np["art_url"] = self._np_art_by_track[key]
            # fetched_at is set by the reader (when the position was
            # measured) so the JS progress bar extrapolates correctly even
            # across the throttled Spotify reads — don't overwrite it here.
            np.setdefault("fetched_at", int(now * 1000))
            # Volume level for the panel: Spotify reader already includes it;
            # for local/app sources read the per-app volume via Core Audio,
            # throttled (pycaw enumeration is relatively costly). Never read
            # the mixer level for Spotify — its real volume is the Web API
            # one (so the slider matches Spotify's own slider).
            if (np.get("source") != "spotify" and not is_spotify_app
                    and np.get("volume") is None):
                if now - self._np_last_vol_t >= 5.0:
                    self._np_last_vol_t = now
                    try:
                        self._np_last_vol = self.engine.read_app_volume()
                    except Exception:  # noqa: BLE001
                        self._np_last_vol = None
                np["volume"] = self._np_last_vol
            self.engine.now_playing = np
            self._np_misses = 0
        else:
            # Don't blank on a single transient miss — hold the last track
            # (paused) and only clear after several seconds of nothing.
            self._np_misses += 1
            if self._np_misses == 8 and not can_spotify:
                if now - getattr(self, "_np_nosrc_t", 0) > 300:
                    self._np_nosrc_t = now
                    self._log("[i] nothing to display: no Windows media "
                              "session, and the Spotify Web API isn't "
                              "authorized (Spotify tab → Test / Authorize).")
            if self._np_misses >= 8:
                self.engine.now_playing = {
                    "title": None, "artist": None, "art_url": None,
                    "progress_ms": 0, "duration_ms": 0, "is_playing": False,
                    "source": None, "fetched_at": int(now * 1000),
                }
            elif self.engine.now_playing.get("title"):
                last = dict(self.engine.now_playing)
                last["is_playing"] = False
                self.engine.now_playing = last
        # Push to any open overlay (mini / taskbar bar). The overlays do
        # NOT poll our js_api — a 2nd pywebview window calling back into
        # Python every second saturates the shared GUI-thread bridge and
        # freezes the whole app. Pushing one-way (Python -> JS) avoids that.
        self._push_overlays(self.engine.now_playing)

    # -- health telemetry --------------------------------------------------
    def _health_loop(self):
        """Log a '[health] …' line every 30s: process memory / handles /
        threads, UI heartbeat, now-playing tick timing, and Spotify Web API
        call stats. Written for correlating 'ping spikes / stutter after a
        while' reports with what the app was actually doing at that moment."""
        while not self._np_stop.is_set():
            self._np_stop.wait(30)
            if self._np_stop.is_set():
                break
            try:
                self._log(self._health_line())
            except Exception:  # noqa: BLE001
                pass

    def _health_line(self):
        parts = []
        if sys.platform.startswith("win"):
            try:
                import ctypes
                from ctypes import wintypes

                class PMC(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                    ]

                proc = ctypes.windll.kernel32.GetCurrentProcess()
                pmc = PMC()
                pmc.cb = ctypes.sizeof(PMC)
                if ctypes.windll.psapi.GetProcessMemoryInfo(
                        proc, ctypes.byref(pmc), pmc.cb):
                    parts.append(f"mem {pmc.WorkingSetSize // (1024 * 1024)}MB")
                count = wintypes.DWORD()
                if ctypes.windll.kernel32.GetProcessHandleCount(
                        proc, ctypes.byref(count)):
                    parts.append(f"handles {count.value}")
            except Exception:  # noqa: BLE001
                pass
        parts.append(f"threads {threading.active_count()}")
        if self._last_poll_t:
            gap = time.time() - self._last_poll_t
            parts.append(f"ui-poll {gap:.0f}s ago"
                         + (" (hidden window pauses it)" if gap > 60 else ""))
        n, total, mx = self._tick_n, self._tick_total, self._tick_max
        self._tick_n, self._tick_total, self._tick_max = 0, 0.0, 0.0
        if n:
            parts.append(f"np-tick avg {total / n * 1000:.0f}ms "
                         f"max {mx * 1000:.0f}ms")
        net = self.engine.net_snapshot()
        if net["n"]:
            parts.append(f"spotify-api {net['n']} calls "
                         f"avg {net['ms'] / net['n']:.0f}ms "
                         f"max {net['mx']:.0f}ms err {net['err']}")
        else:
            parts.append("spotify-api 0 calls")
        return "[health] " + " · ".join(parts)

    def _push_overlays(self, np):
        """Send now-playing to each VISIBLE overlay window. Called from the
        now-playing loop, so it must NEVER block:

        - pywebview's evaluate_js can wait forever on a hidden/suspended
          WebView2 renderer (WebView2 throttles hidden windows, and 'closing'
          an overlay only hides it) — one wedged call used to freeze the whole
          watcher, leaving the panel stuck on the previous song.
        - So the actual evaluate_js runs on a throwaway thread; if that thread
          is still busy at the next tick, the push is skipped and (throttled)
          logged instead of piling up or blocking.

        Every outcome is observable: the injected expression returns a marker
        ('ok' / 'no-render-fn'), and failures land in the Log. The overlays
        also self-heal: if no push arrives for ~10s they fall back to a slow
        poll_np pull."""
        targets = [(kind, win) for kind, win
                   in (("mini", self.mini_window), ("bar", self.bar_window))
                   if win is not None and self._overlay_visible.get(kind)]
        if not targets:
            return
        sig = self._np_sig(np)
        now = time.time()
        if sig == self._overlay_sig and (now - self._overlay_sig_t) < 5:
            return
        self._overlay_sig = sig
        self._overlay_sig_t = now
        prev = self._push_thread
        if prev is not None and prev.is_alive():
            if now - getattr(self, "_push_stuck_t", 0) > 60:
                self._push_stuck_t = now
                self._log("[!] overlay push still in flight — skipping this "
                          "one (an overlay window may be suspended)")
            return
        try:
            payload = json.dumps(np or {})
        except Exception:  # noqa: BLE001
            return
        script = (f"window.mhkRender ? (window.mhkRender({payload}), 'ok') "
                  ": 'no-render-fn'")
        self._push_thread = threading.Thread(
            target=self._do_push, args=(script, targets), daemon=True)
        self._push_thread.start()

    def _do_push(self, script, targets):
        for kind, win in targets:
            try:
                result = win.evaluate_js(script)
                problem = None if result == "ok" else f"returned {result!r}"
            except Exception as exc:  # noqa: BLE001
                problem = f"{type(exc).__name__}: {exc}"
            if problem:
                key = f"_push_err_t_{kind}"
                now = time.time()
                if now - getattr(self, key, 0) > 60:
                    setattr(self, key, now)
                    self._log(f"[!] {kind} overlay push failed: {problem}")

    # -- config -----------------------------------------------------------
    def _apply(self, cfg):
        """Merge a config dict from the UI into the live config and persist."""
        if cfg:
            cfg.pop("__proto__", None)
            _merge(self.config, cfg)
        try:
            save_config(self.config)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[!] couldn't save config: {exc}")
        # keep the engine pointed at the same (now-updated) config object,
        # and sync its Discord webhook so a saved URL takes effect immediately
        # on the already-running engine (not just after a restart).
        self.engine.config = self.config
        self.engine.discord.webhook_url = (
            self.config.get("discord", {}).get("webhook_url", "") or "").strip()
        new_paused = bool(self.config.get("discord", {}).get("paused", False))
        if new_paused != self.engine.discord.paused:
            self._log(f"[i] Discord webhooks {'paused' if new_paused else 'resumed'}.")
        self.engine.discord.paused = new_paused

    def get_state(self):
        return {
            "config": self.config,
            "caps": Engine.capabilities(),
            "running": self.engine.running,
            "mode": self.engine.mode,
            "config_path": config_path(),
            "version": __version__,
        }

    @staticmethod
    def _np_sig(np):
        """A cheap fingerprint of what the UI actually renders — used to avoid
        re-serializing the (often large, data-URL cover) now-playing payload
        across the bridge every second. The JS extrapolates the progress bar
        locally from fetched_at, so it only needs a fresh payload when the
        track / play-state / volume / art changes."""
        np = np or {}
        art = np.get("art_url") or ""
        return (np.get("title"), np.get("artist"), np.get("is_playing"),
                np.get("volume"), np.get("duration_ms"), len(art))

    def poll(self):
        self._last_poll_t = time.time()   # UI heartbeat for the health line
        # Lightweight: NO logs here (they're large and would cross the bridge
        # every second). The UI fetches logs via get_logs() only on the Log tab.
        # And only ship now_playing when it actually changed (or every ~5s as a
        # drift correction) so the heavy cover-art payload doesn't marshal
        # across the bridge on every 1s tick — that churn is what made the
        # window randomly stutter / stop responding.
        np = self.engine.now_playing
        sig = self._np_sig(np)
        now = time.time()
        send = sig != self._np_np_sig or (now - self._np_np_sig_t) > 5
        if send:
            self._np_np_sig = sig
            self._np_np_sig_t = now
        return {
            "running": self.engine.running,
            "mode": self.engine.mode,
            "caps": Engine.capabilities(),
            "now_playing": np if send else None,
            "update": self._update_info,
        }

    def get_logs(self):
        with self._log_lock:
            return {"logs": list(self.logs)}

    # -- updates ----------------------------------------------------------
    def check_update(self):
        res = updater.check()
        res["version"] = __version__
        res["frozen"] = updater.is_frozen()
        self._update_info = res
        if res.get("error"):
            self._log(f"[update] check failed: {res['error']}")
        elif res.get("available"):
            self._log("[update] a new version is available on GitHub.")
        else:
            self._log("[update] you're up to date.")
        return res

    def apply_update(self):
        ok, msg = updater.apply_update(progress=lambda m: self._log(f"[update] {m}"))
        self._log(f"[update] {msg}")
        if ok:
            self._update_info = {"available": False, "installed": True,
                                 "version": __version__}
        return {"ok": ok, "msg": msg}

    def relaunch(self):
        if updater.relaunch():
            self._quit_app()
            return {"ok": True}
        return {"ok": False, "msg": "Couldn't relaunch automatically — please "
                                    "reopen MediaHotKey."}

    def get_changelog(self):
        return CHANGELOG

    def _launch_update_check(self):
        res = self.check_update()
        if res.get("available") and self.config["settings"].get("auto_install_updates"):
            self._log("[update] auto-installing…")
            ok, _ = updater.apply_update(progress=lambda m: self._log(f"[update] {m}"))
            if ok:
                self._log("[update] restarting to apply…")
                if updater.relaunch():
                    self._quit_app()

    def save_config(self, cfg):
        self._apply(cfg)
        return {"ok": True}

    def clear_log(self):
        with self._log_lock:
            self.logs.clear()
        return {"ok": True}

    def open_log_file(self):
        """Open the persistent activity.log (in the app's data folder) so the
        full history — including past sessions — can be read or shared."""
        try:
            if not os.path.exists(self._log_path):
                return {"ok": False, "msg": "No log file yet."}
            if sys.platform.startswith("win"):
                os.startfile(self._log_path)  # noqa: S606
            else:
                import subprocess
                subprocess.Popen(["xdg-open", self._log_path])
            return {"ok": True, "msg": self._log_path}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": f"{exc} — the file is at {self._log_path}"}

    # -- engine lifecycle -------------------------------------------------
    def toggle_engine(self, cfg=None):
        self._apply(cfg)
        if self.engine.running:
            self.engine.stop()
            return {"ok": True, "running": False}
        # rebuild so the latest hotkeys / settings take effect
        mode = self.engine.mode
        self.engine = Engine(self.config, log=self._log,
                             on_mode_change=lambda m: None)
        self.engine.mode = mode
        try:
            self.engine.start()
        except Exception as exc:  # noqa: BLE001
            self._log(f"[!] {exc}")
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "running": True}

    def transport(self, action, cfg=None):
        self._apply(cfg)
        # Control whatever the panel is actually showing: a Web-API (remote)
        # track via the Web API, anything local via the SMTC session directly
        # (works on the Spotify desktop app / browser without Premium).
        src = (self.engine.now_playing or {}).get("source")
        try:
            if src == "spotify":
                {"next": self.engine.sp_next, "prev": self.engine.sp_prev,
                 "playpause": self.engine.sp_playpause}[action]()
            else:
                self.engine.transport_active(action)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[!] transport {action}: {exc}")
        return {"ok": True}

    def add_to_playlist(self):
        # Add the current Spotify track to its playlist (or Liked Songs) — same
        # as the Alt+F9 hotkey. Works via the Web API on free accounts too.
        self.engine.sp_add()
        return {"ok": True}

    def like(self):
        self.engine.sp_like()
        return {"ok": True}

    def volume(self, direction):
        self.engine.volume(10 if direction == "up" else -10)
        return {"ok": True}

    def set_volume(self, percent):
        self.engine.set_volume(percent)
        return {"ok": True}

    # -- mini player (separate always-on-top overlay window) --------------
    def poll_np(self):
        """Lightweight feed for an overlay window (mini / taskbar bar) — just
        the now-playing data (the main poll() also returns logs/state, which is
        needless traffic across a second window's bridge).

        NOTE: takes NO arguments on purpose. A secondary pywebview window
        calling a js_api method WITH an argument on a repeating timer wedges the
        shared bridge (both windows go 'Not Responding'), so overlays call this
        arg-less and we just hand back the current now-playing every tick — the
        overlay is short-lived, so the extra traffic is negligible."""
        return {"now_playing": self.engine.now_playing}

    def open_mini(self):
        # Create the window directly on the GUI thread (NOT a worker thread —
        # a WebView2 window made off-thread appears but never pumps messages,
        # which looked like a freeze). Create once, then show on later opens.
        try:
            if self.mini_window is None:
                mini_index = os.path.join(_resource_dir(), "mini.html")
                self.mini_window = webview.create_window(
                    "MediaHotKey Mini", url=mini_index, js_api=self,
                    width=300, height=448, resizable=False, frameless=True,
                    on_top=True, background_color="#F6EFE1")

                def _closed(*_a):
                    self.mini_window = None
                    self._overlay_visible["mini"] = False
                try:
                    self.mini_window.events.closed += _closed
                except Exception:  # noqa: BLE001
                    pass
            else:
                self.mini_window.show()
        except Exception as exc:  # noqa: BLE001
            self._log(f"[!] mini player: {exc}")
            self.mini_window = None
            self._overlay_visible["mini"] = False
            return {"ok": False, "msg": str(exc)}
        self._overlay_visible["mini"] = True
        self._overlay_sig = None   # force an immediate now-playing push
        return {"ok": True}

    def close_mini(self):
        # Closing only HIDES the window (cheap re-open). Mark it not-visible so
        # pushes stop — WebView2 suspends hidden renderers and a push into one
        # can hang.
        self._overlay_visible["mini"] = False
        if self.mini_window is not None:
            try:
                self.mini_window.hide()
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True}

    def open_bar(self):
        """A tiny horizontal strip (cover + title + prev/play/next) meant to
        hover over the Windows taskbar while gaming in windowed mode. Same
        rules as open_mini: created directly on the GUI thread, once."""
        try:
            if self.bar_window is None:
                bar_index = os.path.join(_resource_dir(), "bar.html")
                self.bar_window = webview.create_window(
                    "MediaHotKey Bar", url=bar_index, js_api=self,
                    width=460, height=54, resizable=False, frameless=True,
                    on_top=True, background_color="#F6EFE1")

                def _closed(*_a):
                    self.bar_window = None
                    self._overlay_visible["bar"] = False
                try:
                    self.bar_window.events.closed += _closed
                except Exception:  # noqa: BLE001
                    pass
            else:
                self.bar_window.show()
        except Exception as exc:  # noqa: BLE001
            self._log(f"[!] taskbar player: {exc}")
            self.bar_window = None
            self._overlay_visible["bar"] = False
            return {"ok": False, "msg": str(exc)}
        self._overlay_visible["bar"] = True
        self._overlay_sig = None   # force an immediate now-playing push
        return {"ok": True}

    def close_bar(self):
        # See close_mini — hidden renderers must not receive pushes.
        self._overlay_visible["bar"] = False
        if self.bar_window is not None:
            try:
                self.bar_window.hide()
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True}

    # -- tests ------------------------------------------------------------
    def test_spotify(self, cfg=None):
        self._apply(cfg)
        try:
            probe = Engine(self.config, log=self._log)
            try:
                probe._ensure_spotify()          # cached / refreshable sign-in
            except SpotifyNotAuthorized:
                # A fresh browser sign-in is needed. Fail fast on the two
                # things that otherwise hang it forever: a wrong ID/secret pair
                # (the browser tab just shows an error the user may miss), and
                # a squatted redirect port (usually a stuck older
                # MediaHotKey.exe).
                spec = self.config.get("spotify", {})
                bad = _check_spotify_creds(spec.get("client_id", ""),
                                           spec.get("client_secret", ""))
                if bad:
                    return {"ok": False, "msg": bad}
                from urllib.parse import urlparse
                port = urlparse(spec.get("redirect_uri")
                                or "http://127.0.0.1:8888/callback").port or 8888
                if _port_in_use(port):
                    return {"ok": False, "msg":
                            f"Can't start the Spotify sign-in: port {port} is "
                            "already in use — usually a stuck older "
                            "MediaHotKey.exe. Open Task Manager, end any other "
                            "MediaHotKey processes, then click Test / Authorize "
                            "again."}
                # Clear the stale/unrefreshable sign-in so the flow starts clean
                # (a dead cached token otherwise poisons the retry).
                try:
                    os.remove(token_cache_path())
                except OSError:
                    pass
                # Run the approval step OURSELVES (never spotipy's silent
                # blocking flow): log the sign-in link (so it can be opened
                # manually if no tab appears), open the browser, and wait on
                # our own tiny redirect server with a hard time limit.
                auth = probe._build_auth()
                url = auth.get_authorize_url()
                self._log(f"[i] Spotify sign-in link (if no tab opens, copy "
                          f"this into your browser): {url}")
                opened = False
                try:
                    import webbrowser
                    opened = bool(webbrowser.open(url))
                except Exception:  # noqa: BLE001
                    opened = False
                self._log(f"[i] browser tab opened: {'yes' if opened else 'NO'} "
                          "— waiting up to 2 minutes for you to approve…")
                code, err = _wait_for_auth_code(port, timeout=120)
                if err:
                    return {"ok": False, "msg":
                            f"Spotify said no: {err}. If it mentions the "
                            "redirect URI, add exactly "
                            f"{spec.get('redirect_uri') or 'http://127.0.0.1:8888/callback'} "
                            "under Redirect URIs in your Spotify app's settings."}
                if not code:
                    return {"ok": False, "msg":
                            "No approval arrived within 2 minutes. If no "
                            "browser tab opened, copy the sign-in link from "
                            "the Log tab into any browser. If the tab showed "
                            "an error, fix the Redirect URI in your Spotify "
                            "app settings (must be exactly "
                            f"{spec.get('redirect_uri') or 'http://127.0.0.1:8888/callback'})."}
                auth.get_access_token(code, as_dict=False)   # writes the cache
                self._log("[i] Spotify sign-in completed. ✅")
                probe._sp = None
                probe._ensure_spotify()          # now uses the fresh cache
            pb = probe._current()
            if pb and pb.get("item"):
                return {"ok": True, "msg": f"Connected — now playing: {pb['item']['name']}"}
            return {"ok": True, "msg": "Connected to Spotify (nothing playing right now)"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": str(exc)}

    def test_discord(self, cfg=None):
        self._apply(cfg)
        dc = Discord(self.config["discord"].get("webhook_url", ""), log=self._log)
        if not dc.ready():
            return {"ok": False, "msg": "No valid webhook URL set."}
        dc.text("✅ MediaHotKey test message — your webhook works!", "playing")
        return {"ok": True, "msg": "Test message sent. Check your channel."}

    def record_hotkey(self):
        if not KEYBOARD_AVAILABLE:
            return ""
        try:
            return keyboard.read_hotkey(suppress=False)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[!] hotkey capture failed: {exc}")
            return ""

    # -- misc -------------------------------------------------------------
    def open_url(self, url):
        import webbrowser
        webbrowser.open(url)
        return {"ok": True}

    def create_shortcut(self):
        """Create a 'MediaHotKey' shortcut on the Desktop pointing at this app."""
        if not sys.platform.startswith("win"):
            return {"ok": False, "msg": "Desktop shortcuts are Windows-only."}
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        lnk = os.path.join(desktop, "MediaHotKey.lnk")
        icon = _icon_path() or ""
        if updater.is_frozen():
            target, args, workdir = sys.executable, "", os.path.dirname(sys.executable)
            if not icon:
                icon = sys.executable
        else:
            base = os.path.dirname(sys.executable)
            pyw = os.path.join(base, "pythonw.exe")
            target = pyw if os.path.exists(pyw) else sys.executable
            run_py = os.path.join(updater.install_dir(), "run.py")
            args = f'"{run_py}"'
            workdir = updater.install_dir()
        ps = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
            "$s.TargetPath='{target}';$s.Arguments='{args}';"
            "$s.WorkingDirectory='{workdir}';{icon}$s.Save()"
        ).format(
            lnk=lnk.replace("'", "''"),
            target=target.replace("'", "''"),
            args=args.replace("'", "''"),
            workdir=workdir.replace("'", "''"),
            icon=(f"$s.IconLocation='{icon.replace(chr(39), chr(39) * 2)}';" if icon else ""),
        )
        try:
            import subprocess
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           check=True, creationflags=0x08000000)  # CREATE_NO_WINDOW
            self._log("[i] created a Desktop shortcut.")
            return {"ok": True, "msg": "Desktop shortcut created."}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": f"Couldn't create shortcut: {exc}"}

    def choose_mascot(self):
        if not self.window:
            return ""
        try:
            types = ("Image files (*.png;*.jpg;*.jpeg;*.webp;*.gif)",)
            result = self.window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False, file_types=types)
        except Exception:  # noqa: BLE001
            result = None
        if not result:
            return ""
        path = result[0] if isinstance(result, (list, tuple)) else result
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            return ""
        ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                "webp": "webp", "gif": "gif"}.get(ext, "png")
        url = f"data:image/{mime};base64," + base64.b64encode(data).decode()
        self.config.setdefault("mascot", {})["image"] = url
        try:
            save_config(self.config)
        except Exception:  # noqa: BLE001
            pass
        return url

    # -- close-to-tray ----------------------------------------------------
    def on_closing(self):
        """Window X pressed. Hide to the tray and keep running instead of
        quitting (so the hotkeys stay alive). Returning False cancels the
        close in pywebview. Without tray support, quit normally.

        This is called by WinForms via pythonnet, so it MUST NOT raise — an
        uncaught exception here surfaces as a fatal .NET error dialog. Any
        failure falls through to letting the window close."""
        try:
            if self._allow_close:
                return True
            if _tray_mods() is None:
                self._allow_close = True
                self._np_stop.set()
                try:
                    if self.engine.running:
                        self.engine.stop()
                except Exception:  # noqa: BLE001
                    pass
                return True            # let the in-progress close proceed
            self._hide_to_tray()
            return False
        except Exception:  # noqa: BLE001
            return True                # never crash the app on close

    def _hide_to_tray(self):
        try:
            self.window.hide()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._ensure_tray()
        except Exception:  # noqa: BLE001
            pass
        self._log("[i] minimized to tray — hotkeys still active. "
                  "Use the tray icon to reopen or quit.")

    def show_window(self):
        try:
            self.window.show()
        except Exception:  # noqa: BLE001
            pass

    def _ensure_tray(self):
        mods = _tray_mods()
        if mods is None or self._tray is not None:
            return
        pystray, Image = mods["pystray"], mods["Image"]
        icon_img = None
        path = _icon_path()
        if path:
            try:
                icon_img = Image.open(path)
            except Exception:  # noqa: BLE001
                icon_img = None
        if icon_img is None:
            icon_img = Image.new("RGB", (64, 64), "#CC7E4F")

        def _safe(fn):
            def handler(icon, item):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass
            return handler

        menu = pystray.Menu(
            pystray.MenuItem("Open MediaHotKey", _safe(self.show_window), default=True),
            pystray.MenuItem("Start / Stop hotkeys", _safe(lambda: self.toggle_engine(None))),
            pystray.MenuItem("Quit", _safe(self._quit_app)),
        )
        self._tray = pystray.Icon("MediaHotKey", icon_img, "MediaHotKey", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _quit_app(self):
        self._allow_close = True
        self._np_stop.set()
        try:
            if self.engine.running:
                self.engine.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:  # noqa: BLE001
                pass
        for attr in ("mini_window", "bar_window"):
            win = getattr(self, attr, None)
            if win is not None:
                try:
                    win.destroy()
                except Exception:  # noqa: BLE001
                    pass
                setattr(self, attr, None)
        try:
            self.window.destroy()
        except Exception:  # noqa: BLE001
            pass


def _has_webview2():
    """Best-effort check for the Edge WebView2 runtime (Windows only).

    pywebview's renderer on Windows is WebView2, which is *not* always present
    on Windows 10. If it's missing the window opens but never renders and looks
    'not responding'. Returns True/False; on any uncertainty returns True so we
    don't block a machine that actually has it."""
    if not sys.platform.startswith("win"):
        return True
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return True
    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    locations = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\\" + guid),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients\\" + guid),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients\\" + guid),
    ]
    for root, path in locations:
        try:
            with winreg.OpenKey(root, path) as key:
                pv, _ = winreg.QueryValueEx(key, "pv")
                if pv and pv not in ("", "0.0.0.0"):
                    return True
        except OSError:
            continue
    return False


def _message_box(text, title="MediaHotKey", style=0):
    """Native dialog on Windows; console fallback elsewhere. Returns the
    Win32 result code (1 = OK/Yes, 2 = Cancel)."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            return ctypes.windll.user32.MessageBoxW(0, text, title, style)
        except Exception:  # noqa: BLE001
            pass
    print(f"{title}: {text}")
    return 1


WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"  # Evergreen bootstrapper


def _apply_window_icon(retries=12):
    """Set the real app icon on the window + taskbar at runtime, so launching
    via pythonw shows our icon instead of the generic Python one. (The frozen
    .exe already carries the icon via PyInstaller --icon.)"""
    if not sys.platform.startswith("win"):
        return
    ico = _icon_path()
    if not ico:
        return
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.FindWindowW.restype = wintypes.HWND
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.LoadImageW.restype = wintypes.HANDLE
        u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
        # PostMessageW (not SendMessageW) so this can never block on a
        # cross-thread send if the GUI thread is momentarily busy.
        u.PostMessageW.restype = wintypes.BOOL
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
        title = f"MediaHotKey {__version__}"
        IMAGE_ICON, WM_SETICON = 1, 0x0080
        ICON_SMALL, ICON_BIG = 0, 1
        LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x0010, 0x0040
        for _ in range(retries):
            hwnd = u.FindWindowW(None, title)
            if hwnd:
                big = u.LoadImageW(None, ico, IMAGE_ICON, 0, 0,
                                   LR_LOADFROMFILE | LR_DEFAULTSIZE)
                small = u.LoadImageW(None, ico, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
                if big:
                    u.PostMessageW(hwnd, WM_SETICON, ICON_BIG, big)
                if small:
                    u.PostMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
                return
            time.sleep(0.3)
    except Exception:  # noqa: BLE001
        pass


def main():
    if webview is None:
        _message_box(
            "pywebview isn't installed.\n\nOpen a terminal in the MediaHotKey "
            "folder and run:\n\n    pip install -r requirements.txt",
            "MediaHotKey — missing dependency")
        sys.exit(1)

    # If the WebView2 runtime is missing, a frameless window would just hang.
    # Tell the user and offer to download it instead of trapping them.
    if not _has_webview2():
        MB_OKCANCEL = 0x1
        MB_ICONWARNING = 0x30
        res = _message_box(
            "MediaHotKey needs the Microsoft Edge WebView2 Runtime to draw its "
            "window (it's missing on some Windows 10 PCs).\n\n"
            "Click OK to open the free download page, then install it and "
            "reopen MediaHotKey.\n\n"
            "Click Cancel to try launching anyway.",
            "MediaHotKey — one-time setup", MB_OKCANCEL | MB_ICONWARNING)
        if res == 1:  # OK → download
            import webbrowser
            webbrowser.open(WEBVIEW2_URL)
            sys.exit(0)
        # Cancel → fall through and attempt to start regardless.

    # Give the app its own taskbar identity (so Windows uses our icon and
    # groups it correctly rather than under "Python").
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "MediaHotKey.App")
        except Exception:  # noqa: BLE001
            pass
        # WebView2 can stall for ~a minute on a cold start while it does
        # background networking / SmartScreen checks. Turn those off so the
        # window comes up promptly.
        os.environ.setdefault(
            "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
            "--disable-background-networking --disable-component-update "
            "--disable-features=msSmartScreenProtection,EdgeCollections,"
            "msWebOOUI,msPdfOOUI")

    api = Api()
    index = os.path.join(_resource_dir(), "index.html")
    settings = api.config["settings"]
    # Optimistic — the tray import is deferred to _post_init (worker thread);
    # if it turns out to be unavailable there, the window is simply shown.
    start_hidden = bool(settings.get("start_minimized"))

    # Create the window NORMALLY (never hidden at creation) — a hidden WebView2
    # window can stall its own initialization on a cold start and wedge the main
    # GUI thread, which makes the tray icon unresponsive. We hide to the tray
    # only AFTER the page has finished loading (below).
    window = webview.create_window(
        f"MediaHotKey {__version__}",
        url=index,
        js_api=api,
        width=1120,
        height=860,
        min_size=(720, 520),     # can be shrunk small or dragged large
        resizable=True,
        frameless=False,         # native frame → resize from edges + min/max/close
        background_color="#F6EFE1",
    )
    api.window = window
    t_window = time.time()

    # Closing the X hides to the tray (hotkeys keep running) instead of quitting.
    try:
        window.events.closing += api.on_closing
    except Exception:  # noqa: BLE001
        pass

    api._did_init = False
    init_lock = threading.Lock()

    def _log_startup_timing():
        """One '[start] …' line in the Log tab showing where launch time went,
        so slow-startup reports come with real numbers instead of guesses."""
        try:
            parts = []
            if getattr(sys, "_MEIPASS", None):
                # The onefile bootloader creates _MEI… right when it starts
                # extracting, so its ctime approximates "double-click time".
                try:
                    unpack = _T_START - os.path.getctime(sys._MEIPASS)
                    if 0 < unpack < 600:
                        parts.append(f"exe unpack ~{unpack:.1f}s")
                except OSError:
                    pass
            parts.append(f"imports {_T_IMPORTS - _T_START:.1f}s")
            parts.append(f"window+page {time.time() - t_window:.1f}s")
            api._log("[start] " + " · ".join(parts))
        except Exception:  # noqa: BLE001
            pass

    def _post_init():
        # IMPORTANT: this must never run on the main GUI thread. The
        # edgechromium backend dispatches events and the JS<->Python bridge on
        # the main thread, so any blocking work here would freeze the window
        # ("Not Responding") and stall get_state(), leaving the UI blank. We
        # always run it on a worker thread (see _kick) and only touch the
        # window through pywebview's thread-safe methods.
        with init_lock:
            if api._did_init:
                return
            api._did_init = True
        _log_startup_timing()
        # Hide to tray FIRST so the window doesn't linger on screen while the
        # rest of init runs. (The tray import is lazy — if it fails, just keep
        # the window visible instead.)
        if start_hidden:
            api._ensure_tray()
            if api._tray is not None:
                try:
                    api.window.hide()
                except Exception:  # noqa: BLE001
                    pass
                api._log("[i] started minimized to the system tray.")
        _apply_window_icon()
        # Clean up any leftovers from a previous self-update (e.g. the old exe).
        # Runs here (worker thread, post-load) instead of before the window so
        # file I/O can't delay the launch.
        try:
            updater.cleanup_stale()
        except Exception:  # noqa: BLE001
            pass
        if settings.get("start_engine_on_launch"):
            try:
                api.engine.start()
            except Exception as exc:  # noqa: BLE001
                api._log(f"[!] {exc}")
        # Start the now-playing watcher now that the window is up, and defer the
        # update check a few seconds so neither competes with WebView2's init.
        api.start_now_playing()
        threading.Thread(target=api._health_loop, daemon=True).start()
        if settings.get("update_check_on_launch"):
            threading.Timer(
                5.0,
                lambda: threading.Thread(target=api._launch_update_check,
                                         daemon=True).start()).start()

    def _kick(*_a):
        # Offload to a worker thread so we never block the main GUI thread.
        threading.Thread(target=_post_init, daemon=True).start()

    # Primary trigger: page-loaded event. Secondary: a timed fallback in case
    # the event never arrives. Both just kick the worker; _did_init dedupes.
    try:
        window.events.loaded += _kick
    except Exception:  # noqa: BLE001
        pass

    def _fallback():
        time.sleep(3.0)
        _kick()

    # Force the modern Chromium (WebView2) backend on Windows so the UI's
    # modern JS runs; fall back gracefully on other platforms / old pywebview.
    gui = "edgechromium" if sys.platform.startswith("win") else None

    # A persistent, fast local data folder lets WebView2 reuse its profile
    # across launches instead of cold-initializing every time.
    storage = os.path.join(
        os.environ.get("LOCALAPPDATA") or config_dir(), "MediaHotKey", "webview")
    try:
        os.makedirs(storage, exist_ok=True)
    except Exception:  # noqa: BLE001
        storage = None
    start_kwargs = {"private_mode": False}
    if storage:
        start_kwargs["storage_path"] = storage
    if gui:
        start_kwargs["gui"] = gui

    try:
        try:
            webview.start(_fallback, **start_kwargs)
        except TypeError:
            # older pywebview without some kwargs
            webview.start(_fallback, gui=gui) if gui else webview.start(_fallback)
    except Exception as exc:  # noqa: BLE001
        _message_box(
            "MediaHotKey couldn't open its window.\n\n"
            f"Details: {exc}\n\n"
            "Most often this means the Edge WebView2 Runtime is missing — "
            "install it from:\n" + WEBVIEW2_URL,
            "MediaHotKey — startup error")
        raise


if __name__ == "__main__":
    main()
