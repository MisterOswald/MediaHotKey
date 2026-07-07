"""
The MediaHotKey engine.

This is the original script's logic refactored into a single, controllable
object. The UI creates one Engine from the saved config, then calls start()
to register the global hotkeys / poller and stop() to tear them down — all
without touching the source code.

Two modes:

  SPOTIFY MODE  full control via the Spotify Web API (skip / prev / play-pause
                / like / add-to-playlist), Discord embeds and cross-device
                now-playing tracking. Requires Spotify Premium for playback
                control.

  MEDIA MODE    universal transport control via Windows System Media Transport
                Controls (SMTC). Controls whatever is playing (SoundCloud in a
                browser, YouTube, etc.) without injecting keystrokes. Transport
                only — no like / add. Needs `pip install winsdk` on Windows.
"""

import time
import base64
import asyncio
import threading
import os

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import token_cache_path
from .discord_notify import Discord, COLORS

# Optional deps — imported lazily-ish so the UI can still run and report status
# even when they are missing (e.g. on a fresh machine before pip install).
try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except Exception:  # noqa: BLE001
    keyboard = None
    KEYBOARD_AVAILABLE = False

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    SPOTIPY_AVAILABLE = True
except Exception:  # noqa: BLE001
    spotipy = None
    SpotifyOAuth = None
    SPOTIPY_AVAILABLE = False

# SMTC (universal media control) — Windows only.
try:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    )
    from winsdk.windows.storage.streams import (
        Buffer, DataReader, InputStreamOptions,
    )
    MEDIA_AVAILABLE = True
except Exception:  # noqa: BLE001
    MediaManager = None
    MEDIA_AVAILABLE = False

# Per-application volume (Windows Core Audio). pycaw/comtypes are SLOW to
# import (comtypes generates COM wrappers), so load them lazily on first use
# instead of at startup — importing them eagerly noticeably delayed launch.
_PYCAW = None  # None = not tried yet; {} = tried and unavailable; dict = loaded


def _pycaw():
    global _PYCAW
    if _PYCAW is None:
        try:
            import comtypes
            from pycaw.pycaw import (AudioUtilities, ISimpleAudioVolume,
                                     IAudioMeterInformation)
            _PYCAW = {"comtypes": comtypes, "AudioUtilities": AudioUtilities,
                      "ISimpleAudioVolume": ISimpleAudioVolume,
                      "IAudioMeterInformation": IAudioMeterInformation}
        except Exception:  # noqa: BLE001
            _PYCAW = {}
    return _PYCAW or None

SCOPE = (
    "user-modify-playback-state user-read-playback-state "
    "user-library-modify playlist-modify-public playlist-modify-private"
)


class SpotifyNotAuthorized(RuntimeError):
    """No cached Spotify sign-in usable from background code — the user needs
    to click Test / Authorize on the Spotify tab (the only interactive path)."""


if SPOTIPY_AVAILABLE:
    class _NonInteractiveOAuth(SpotifyOAuth):
        """SpotifyOAuth that can NEVER prompt. spotipy's get_access_token falls
        back to an interactive flow (browser + console input()) whenever the
        cached token is missing or its refresh fails — in the windowed exe that
        surfaces as 'RuntimeError: input(): lost sys.stdin' and silently killed
        every Spotify feature until restart. Raising instead makes the failure
        loud, recoverable and actionable (re-run Test / Authorize)."""

        def get_auth_response(self, *_a, **_k):
            raise SpotifyNotAuthorized(
                "Spotify sign-in expired or was revoked — open the Spotify "
                "tab and click Test / Authorize to sign in again.")
else:
    _NonInteractiveOAuth = None

# Hard cap on any single Spotify Web API request. Without this, spotipy uses no
# timeout, so a slow/stalled network call blocks the now-playing thread — the
# panel stops updating until the socket finally returns ("loads after a while").
SPOTIFY_TIMEOUT = 6
# Ceiling for a single Windows-media (SMTC/WinRT) read or control call, so a
# stuck native call can't wedge the now-playing loop or a hotkey thread.
SMTC_TIMEOUT = 5


def _build_session():
    """A requests session that auto-retries stale/idle connections."""
    session = requests.Session()
    retry_kwargs = dict(
        total=3, connect=3, read=3,
        backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
    )
    try:
        retries = Retry(allowed_methods=None, **retry_kwargs)
    except TypeError:
        retries = Retry(method_whitelist=None, **retry_kwargs)
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class Engine:
    """Owns the hotkeys, Spotify client, media control and Discord posting."""

    def __init__(self, config, log=print, on_mode_change=None):
        self.config = config
        self._log = log
        self.on_mode_change = on_mode_change

        self.mode = config["settings"].get("start_mode", "spotify")
        self.discord = Discord(config["discord"].get("webhook_url", ""), log=self._log)

        self._sp = None
        self._running = False
        self._hotkey_handles = []
        self._poller_thread = None
        self._stop_event = threading.Event()

        # Shared current_playback cache (watcher + poller) and Web API call
        # accounting for the [health] telemetry line.
        self._pb_cache = (None, 0.0)
        self._net = {"n": 0, "ms": 0.0, "mx": 0.0, "err": 0}

        self._seen_lock = threading.Lock()
        self._seen = {"track_id": None, "is_playing": None}
        self._last_add = {"track": None, "playlist": None}

        # Last-known track, surfaced to the UI's now-playing panel.
        self.now_playing = {
            "title": None, "artist": None, "art_url": None,
            "progress_ms": 0, "duration_ms": 0, "is_playing": False,
            "source": None, "fetched_at": 0,
        }
        # Cache the decoded SMTC cover so we don't re-encode it every poll.
        self._np_art_cache = {}
        # De-duplicated diagnostic logging (logs only when a message changes).
        self._dbg_last = {}

    def _dbg(self, tag, msg):
        """Local-only diagnostic log. Off by default — set self.debug = True to
        re-enable (it was noisy and added log churn)."""
        if not getattr(self, "debug", False):
            return
        if self._dbg_last.get(tag) == msg:
            return
        self._dbg_last[tag] = msg
        self.log(f"[dbg:{tag}] {msg}")

    # ------------------------------------------------------------------ log
    def log(self, msg):
        try:
            self._log(msg)
        except Exception:  # noqa: BLE001
            pass

    def notify_text(self, msg, color_key="info"):
        self.log(msg)
        self.discord.text(msg, color_key)

    def notify_track(self, *args, **kwargs):
        label = args[0] if args else kwargs.get("action_label", "")
        track = args[1] if len(args) > 1 else kwargs.get("track", {})
        artists = ", ".join(a["name"] for a in track.get("artists", []))
        self.log(f"[ok] {label}: {artists} - {track.get('name', '')}")
        self.discord.track(*args, **kwargs)

    def notify_media(self, label, title, artist, **kwargs):
        self.log(f"[ok] {label}: {title} - {artist}")
        self.discord.media(label, title, artist, **kwargs)

    # ---------------------------------------------------------- capabilities
    @staticmethod
    def capabilities():
        return {
            "keyboard": KEYBOARD_AVAILABLE,
            "spotipy": SPOTIPY_AVAILABLE,
            "media": MEDIA_AVAILABLE,
        }

    # ------------------------------------------------------------- spotify
    def _build_auth(self):
        """The SpotifyOAuth used everywhere. open_browser=False on purpose:
        NOTHING may fall into spotipy's built-in interactive flow (it blocks
        forever on a local socket with no diagnostics) — the app runs the
        approval step itself in the Spotify tab's Test/Authorize."""
        if not SPOTIPY_AVAILABLE:
            raise RuntimeError("spotipy not installed — run: pip install spotipy")
        spec = self.config["spotify"]
        if not spec.get("client_id") or not spec.get("client_secret"):
            raise RuntimeError("Spotify Client ID / Secret not set — open Settings.")
        return _NonInteractiveOAuth(
            client_id=spec["client_id"],
            client_secret=spec["client_secret"],
            redirect_uri=spec.get("redirect_uri", "http://127.0.0.1:8888/callback"),
            scope=SCOPE,
            cache_path=token_cache_path(),
            open_browser=False,
            requests_timeout=SPOTIFY_TIMEOUT,   # token refresh must not stall
        )

    def _ensure_spotify(self):
        """Build the Spotify client on first use. Only a cached/refreshable
        sign-in is accepted — when it's missing or can't be refreshed (e.g. the
        client secret was regenerated), raise SpotifyNotAuthorized; callers
        report it and move on. The interactive sign-in lives in the UI's
        Test/Authorize flow exclusively."""
        if self._sp is not None:
            return self._sp
        auth = self._build_auth()
        token = None
        try:
            token = auth.validate_token(auth.cache_handler.get_cached_token())
        except Exception:  # noqa: BLE001 — unrefreshable/corrupt cache
            token = None
        if not token:
            raise SpotifyNotAuthorized(
                "Spotify isn't authorized — open the Spotify tab and click "
                "Test / Authorize.")
        session = _build_session()
        session.hooks.setdefault("response", []).append(self._net_hook)
        self._sp = spotipy.Spotify(
            requests_session=session,
            requests_timeout=SPOTIFY_TIMEOUT,
            retries=0,
            auth_manager=auth,
        )
        return self._sp

    def _net_hook(self, response, *_a, **_k):
        """requests response hook — per-call Web API accounting for [health]."""
        try:
            st = self._net
            st["n"] += 1
            ms = response.elapsed.total_seconds() * 1000
            st["ms"] += ms
            st["mx"] = max(st["mx"], ms)
            if response.status_code >= 400:
                st["err"] += 1
        except Exception:  # noqa: BLE001
            pass

    def net_snapshot(self):
        """Return and reset the Web API call stats (for the health line)."""
        st = self._net
        self._net = {"n": 0, "ms": 0.0, "mx": 0.0, "err": 0}
        return st

    def _current_cached(self, max_age):
        """current_playback() shared between the now-playing watcher and the
        Discord poller, so the two timers don't each hit the Web API on their
        own.

        Adaptive backoff: when a call comes back slow (>0.8s — the network is
        congested, e.g. mid-game ping spike), polling eases off for a minute
        so the app stays out of the network's way exactly when it matters."""
        pb, t = self._pb_cache
        now = time.time()
        if now < getattr(self, "_api_slow_until", 0):
            max_age = max(max_age * 2, 10.0)
        if t and now - t < max_age:
            return pb
        t0 = time.perf_counter()
        pb = self._ensure_spotify().current_playback(additional_types="episode")
        dt = time.perf_counter() - t0
        if dt > 0.8:
            self._api_slow_until = now + 60
            if now - getattr(self, "_api_slow_log_t", 0) > 120:
                self._api_slow_log_t = now
                self.log(f"[health] Spotify API slow ({dt:.1f}s — network "
                         "congested) — easing off polling for 60s")
        self._pb_cache = (pb, now)
        return pb

    def _current(self):
        return self._ensure_spotify().current_playback()

    @staticmethod
    def _device_name(playback):
        dev = (playback or {}).get("device") or {}
        return dev.get("name")

    def _footer_for(self, playback):
        dev = self._device_name(playback)
        return f"On {dev}" if dev else None

    # --------------------------------------------------------- shared infra
    @staticmethod
    def _run_async(fn):
        threading.Thread(target=fn, daemon=True).start()

    def _safe(self, fn, label):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            if SPOTIPY_AVAILABLE and isinstance(exc, spotipy.exceptions.SpotifyException):
                if exc.http_status == 404:
                    self.notify_text(
                        f"⚠️ {label}: no active Spotify device — press play in "
                        f"Spotify once first.", "error")
                elif exc.http_status == 403:
                    self.notify_text(
                        f"⚠️ {label}: forbidden — playback control requires "
                        f"Spotify Premium.", "error")
                elif exc.http_status == 429:
                    self.log(f"[!] {label}: rate limited — increase Poll interval.")
                else:
                    self.notify_text(f"⚠️ {label}: {exc}", "error")
            elif isinstance(exc, (requests.exceptions.ConnectionError,
                                  requests.exceptions.Timeout)):
                self.log(f"[!] {label}: connection hiccup, please press again")
            else:
                self.notify_text(f"⚠️ {label}: {exc}", "error")

    # ----------------------------------------------------- spotify actions
    def _announce_playback(self, playback):
        if not playback or not playback.get("item"):
            return
        track = playback["item"]
        tid = track["id"]
        is_playing = playback.get("is_playing", False)
        footer = self._footer_for(playback)
        announce_pr = self.config["settings"].get("announce_pause_resume", False)

        # Keep the now-playing panel fresh on every poll (progress included).
        album = track.get("album", {})
        images = album.get("images", [])
        self.now_playing = {
            "title": track.get("name"),
            "artist": ", ".join(a["name"] for a in track.get("artists", [])),
            "art_url": images[0]["url"] if images else None,
            "progress_ms": playback.get("progress_ms") or 0,
            "duration_ms": track.get("duration_ms") or 0,
            "is_playing": is_playing,
            "source": "spotify",
            "fetched_at": int(time.time() * 1000),
        }

        with self._seen_lock:
            track_changed = tid != self._seen["track_id"]
            play_changed = is_playing != self._seen["is_playing"]
            self._seen["track_id"] = tid
            self._seen["is_playing"] = is_playing

        if track_changed:
            if is_playing:
                self.notify_track("🎵 Now Playing", track, "playing", footer=footer)
        elif play_changed and announce_pr:
            if is_playing:
                self.notify_track("▶️ Resumed", track, "playing", footer=footer)
            else:
                self.notify_track("⏸️ Paused", track, "pause", footer=footer)

    def sp_next(self):
        def go():
            # Local Windows-media control first — instant, and avoids the Web
            # API's slow "no active device" round-trip. Web API only as a remote
            # fallback. (Discord now-playing is posted by the background poller.)
            if self._smtc_fallback("next"):
                return
            sp = self._ensure_spotify()
            sp.next_track()
            time.sleep(0.4)
            self._announce_playback(self._current())
        self._run_async(lambda: self._safe(go, "next track"))

    def _smtc_fallback(self, action):
        """Control the local media session directly (Spotify desktop / browser)
        when the Spotify Web API can't (e.g. 'no active device')."""
        if not MEDIA_AVAILABLE:
            return False
        try:
            return bool(self._run_smtc(self._control_active(action)))
        except Exception:  # noqa: BLE001
            return False

    def sp_prev(self):
        def go():
            if self._smtc_fallback("prev"):
                return
            sp = self._ensure_spotify()
            sp.previous_track()
            time.sleep(0.4)
            self._announce_playback(self._current())
        self._run_async(lambda: self._safe(go, "previous track"))

    def sp_playpause(self):
        def toggle():
            # Local Windows-media control first — instant, and avoids the Web
            # API's slow "no active device" round-trip. Web API only as a remote
            # fallback. (Discord now-playing is posted by the background poller.)
            if self._smtc_fallback("playpause"):
                return
            sp = self._ensure_spotify()
            playback = self._current()
            if playback and playback.get("is_playing"):
                sp.pause_playback()
                track = playback.get("item")
                with self._seen_lock:
                    self._seen["is_playing"] = False
                if track:
                    self.notify_track("⏸️ Paused", track, "pause",
                                      footer=self._footer_for(playback))
                else:
                    self.notify_text("⏸️ paused", "pause")
            else:
                sp.start_playback()
                time.sleep(0.4)
                pb = self._current()
                track = pb.get("item") if pb else None
                with self._seen_lock:
                    self._seen["is_playing"] = True
                    if track:
                        self._seen["track_id"] = track["id"]
                if track:
                    self.notify_track("▶️ Playing", track, "playing",
                                      footer=self._footer_for(pb))
                else:
                    self.notify_text("▶️ playing", "playing")
        self._run_async(lambda: self._safe(toggle, "play/pause"))

    def sp_add(self):
        def add():
            sp = self._ensure_spotify()
            playback = self._current()
            track = playback.get("item") if playback else None
            if not track:
                self.notify_text("⚠️ add: nothing currently playing.", "error")
                return
            ctx = playback.get("context")
            if ctx and ctx.get("type") == "playlist":
                playlist_id = ctx["uri"].split(":")[-1]
                if (self._last_add["track"] == track["id"]
                        and self._last_add["playlist"] == playlist_id):
                    self.notify_track("⏳ Already added", track, "info",
                                      footer="Skipped duplicate")
                    return
                try:
                    sp.playlist_add_items(playlist_id, [track["id"]])
                    self._last_add["track"] = track["id"]
                    self._last_add["playlist"] = playlist_id
                    pl = sp.playlist(playlist_id, fields="name")
                    self.notify_track("➕ Added to playlist", track, "add",
                                      footer=f'Playlist: {pl["name"]}')
                except spotipy.exceptions.SpotifyException as exc:
                    if exc.http_status == 403:
                        sp.current_user_saved_tracks_add([track["id"]])
                        self.notify_track("💚 Liked (playlist not editable)", track,
                                          "like", footer="Saved to Liked Songs")
                    else:
                        raise
            else:
                sp.current_user_saved_tracks_add([track["id"]])
                self.notify_track("💚 Liked", track, "like",
                                  footer="Not from a playlist — saved to Liked Songs")
        self._run_async(lambda: self._safe(add, "add to playlist"))

    def sp_like(self):
        def save():
            sp = self._ensure_spotify()
            playback = self._current()
            track = playback.get("item") if playback else None
            if not track:
                self.notify_text("⚠️ like: nothing currently playing.", "error")
                return
            sp.current_user_saved_tracks_add([track["id"]])
            self.notify_track("💚 Liked", track, "like", footer="Saved to Liked Songs")
        self._run_async(lambda: self._safe(save, "like song"))

    def _pycaw_volume_op(self, hint, op, value=None):
        """get/add/set per-app volume via Core Audio. Targets the process whose
        name contains `hint`, else whatever session is making sound. Returns the
        resulting level as 0..1 (or None if nothing to control)."""
        p = _pycaw()
        if not p:
            return None
        comtypes = p["comtypes"]
        AudioUtilities = p["AudioUtilities"]
        ISimpleAudioVolume = p["ISimpleAudioVolume"]
        IAudioMeterInformation = p["IAudioMeterInformation"]
        try:
            comtypes.CoInitialize()
        except Exception:  # noqa: BLE001
            pass
        try:
            sessions = AudioUtilities.GetAllSessions()
            by_hint, audible = [], []
            for s in sessions:
                try:
                    vol = s._ctl.QueryInterface(ISimpleAudioVolume)
                except Exception:  # noqa: BLE001
                    continue
                name = ""
                try:
                    if s.Process:
                        name = (s.Process.name() or "").lower()
                except Exception:  # noqa: BLE001
                    name = ""
                if hint and hint in name:
                    by_hint.append(vol)
                try:
                    meter = s._ctl.QueryInterface(IAudioMeterInformation)
                    if meter.GetPeakValue() > 0.0001:
                        audible.append(vol)
                except Exception:  # noqa: BLE001
                    pass
            targets = by_hint or audible
            if not targets:
                return None
            if op == "get":
                return targets[0].GetMasterVolume()
            for vol in targets:
                cur = vol.GetMasterVolume()
                newv = value if op == "set" else cur + value
                vol.SetMasterVolume(max(0.0, min(1.0, newv)), None)
            return targets[0].GetMasterVolume()
        except Exception as exc:  # noqa: BLE001
            self.log(f"[i] volume (pycaw): {exc}")
            return None
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass

    def _hint(self):
        return (self.config["settings"].get("media_app_hint", "") or "").lower()

    def _is_spotify_now(self):
        """True if the current track is Spotify (by source or the SMTC app id),
        so volume routes through Spotify's own volume rather than the mixer."""
        np = self.now_playing or {}
        return np.get("source") == "spotify" or "spotify" in (np.get("app") or "")

    def read_app_volume(self):
        """Current per-app volume as 0-100, or None (for the now-playing panel)."""
        lvl = self._pycaw_volume_op(self._hint(), "get")
        return None if lvl is None else int(round(lvl * 100))

    def _spotify_set_volume(self, percent):
        sp = self._ensure_spotify()
        sp.volume(max(0, min(100, int(percent))))

    def _spotify_volume_route(self):
        """Whether volume should go through the Spotify Web API right now."""
        return (self._is_spotify_now() and SPOTIPY_AVAILABLE
                and self.config["spotify"].get("client_id")
                and os.path.exists(token_cache_path()))

    def _log_spotify_volume_fail(self, exc):
        """Volume changes are user-initiated — say what failed and where the
        control goes instead; a silent fallback feels like a dead button."""
        if isinstance(exc, SpotifyNotAuthorized):
            self.log("[!] Spotify volume needs authorization (Spotify tab → "
                     "Test / Authorize) — trying the app mixer instead.")
        elif "VOLUME_CONTROL" in str(exc).upper():
            self.log("[!] Spotify says this playback device doesn't allow "
                     "volume control from apps — trying the app mixer instead.")
        else:
            self.log(f"[!] Spotify volume failed ({type(exc).__name__}: {exc}) "
                     "— trying the app mixer instead.")

    def volume(self, delta):
        """Nudge volume by `delta`% — Spotify Web API for a Spotify track,
        otherwise the app's own volume, else system media keys."""
        def go():
            if self._spotify_volume_route():
                try:
                    pb = self._ensure_spotify().current_playback()
                    vol = ((pb or {}).get("device") or {}).get("volume_percent")
                    if vol is not None:
                        newv = max(0, min(100, int(vol) + delta))
                        self._spotify_set_volume(newv)
                        self.now_playing["volume"] = newv   # reflect instantly
                        self.log(f"[ok] Spotify volume {newv}%")
                        return
                    self.log("[!] Spotify reports no volume for the active "
                             "device — trying the app mixer instead.")
                except Exception as exc:  # noqa: BLE001
                    self._log_spotify_volume_fail(exc)
            lvl = self._pycaw_volume_op(self._hint(), "add", delta / 100.0)
            if lvl is not None:
                self.now_playing["volume"] = int(round(lvl * 100))
                self.log(f"[ok] app volume {int(round(lvl * 100))}%")
                return
            if KEYBOARD_AVAILABLE:
                key = "volume up" if delta > 0 else "volume down"
                for _ in range(max(1, abs(int(delta)) // 4)):
                    try:
                        keyboard.send(key)
                    except Exception:  # noqa: BLE001
                        break
                self.log("[ok] sent system volume keys")
            else:
                self.notify_text("⚠️ volume: no control method available", "error")
        self._run_async(lambda: self._safe(go, "volume"))

    def set_volume(self, percent):
        """Set an absolute volume level (0-100) from the UI slider."""
        percent = max(0, min(100, int(percent)))
        def go():
            if self._spotify_volume_route():
                try:
                    self._spotify_set_volume(percent)
                    self.now_playing["volume"] = percent   # reflect instantly
                    self.log(f"[ok] Spotify volume {percent}%")
                    return
                except Exception as exc:  # noqa: BLE001
                    self._log_spotify_volume_fail(exc)
            lvl = self._pycaw_volume_op(self._hint(), "set", percent / 100.0)
            if lvl is not None:
                self.now_playing["volume"] = int(round(lvl * 100))
                self.log(f"[ok] app volume {int(round(lvl * 100))}%")
            else:
                self.log("[!] volume: no audio session found to control — set "
                         "the Media app hint (General tab) to your browser, "
                         "e.g. 'brave' or 'chrome'.")
        self._run_async(lambda: self._safe(go, "set volume"))

    # ------------------------------------------------------- media (SMTC)
    def _pick_session(self, mgr):
        hint = self.config["settings"].get("media_app_hint", "")
        try:
            sessions = mgr.get_sessions()
            if hint:
                for i in range(sessions.size):
                    s = sessions.get_at(i)
                    aumid = (s.source_app_user_model_id or "").lower()
                    if hint.lower() in aumid:
                        return s
        except Exception:  # noqa: BLE001
            pass
        return mgr.get_current_session()

    @staticmethod
    async def _read_thumbnail(props):
        ref = getattr(props, "thumbnail", None)
        if ref is None:
            return None
        stream = await ref.open_read_async()
        size = stream.size
        if not size:
            return None
        buffer = Buffer(size)
        await stream.read_async(buffer, size, InputStreamOptions.READ_AHEAD)
        reader = DataReader.from_buffer(buffer)
        n = buffer.length
        # winsdk's DataReader.read_bytes fills a bytearray you pass in (and
        # returns None); older builds returned bytes for a count argument.
        try:
            out = bytearray(n)
            reader.read_bytes(out)
            return bytes(out)
        except TypeError:
            return bytes(reader.read_bytes(n))

    @staticmethod
    async def _media_do(session, action):
        if action == "next":
            await session.try_skip_next_async()
        elif action == "prev":
            await session.try_skip_previous_async()
        elif action == "playpause":
            await session.try_toggle_play_pause_async()

    async def _run_media(self, action):
        try:
            mgr = await MediaManager.request_async()
        except Exception as exc:  # noqa: BLE001
            self.log(f"[i] media: can't reach the media session manager ({exc})")
            return None
        session = self._pick_session(mgr)
        if session is None:
            return None

        for attempt in range(2):
            try:
                await self._media_do(session, action)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 0:
                    await asyncio.sleep(0.25)
                    try:
                        mgr = await MediaManager.request_async()
                        session = self._pick_session(mgr) or session
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    self.log(f"[i] media {action}: control unavailable ({exc})")

        await asyncio.sleep(0.5)

        title, artist, art = "Unknown", "", None
        try:
            props = await session.try_get_media_properties_async()
            title = props.title or "Unknown"
            artist = props.artist or ""
            try:
                art = await self._read_thumbnail(props)
            except Exception:  # noqa: BLE001
                art = None
        except Exception as exc:  # noqa: BLE001
            self.log(f"[i] media: couldn't read track info ({exc})")
        return (title, artist, art)

    # ---- live now-playing readers (for the UI panel) -------------------
    def _pick_now_playing_session(self, mgr):
        """Choose which media session to *display*. Unlike transport control
        (which honours media_app_hint), the panel should show whatever is
        actually playing — and in Spotify mode it should only ever show the
        Spotify session, so a paused/background browser tab can't hijack the
        card. Returns None in Spotify mode when Spotify isn't a local session,
        letting the caller fall back to the Spotify Web API."""
        try:
            sessions = mgr.get_sessions()
            items = []
            for i in range(sessions.size):
                s = sessions.get_at(i)
                aumid = (s.source_app_user_model_id or "").lower()
                try:
                    status = int(s.get_playback_info().playback_status)
                except Exception:  # noqa: BLE001
                    status = 0
                items.append((aumid, s, status))
        except Exception:  # noqa: BLE001
            return mgr.get_current_session()

        if self.mode == "spotify":
            spotify = [it for it in items if "spotify" in it[0]]
            playing = [it for it in spotify if it[2] == 4]
            if playing:
                return playing[0][1]
            if spotify:
                return spotify[0][1]
            # No Spotify-desktop session. Spotify in a BROWSER (web player)
            # registers under the browser's id — accept any actively PLAYING
            # session so that still shows. (Only playing ones, so a paused
            # background tab can't hijack the card.)
            for aumid, s, status in items:
                if status == 4:
                    return s
            return None  # nothing local → use the Web API instead

        # media mode: prefer the hinted app, then any playing session.
        hint = (self.config["settings"].get("media_app_hint", "") or "").lower()
        if hint:
            for aumid, s, status in items:
                if hint in aumid:
                    return s
        for aumid, s, status in items:
            if status == 4:
                return s
        return mgr.get_current_session()

    async def _smtc_snapshot(self):
        """Read the current Windows media session: title/artist/art/position.

        This sees the Spotify *desktop app* and browser media alike, so it's
        the most universal source on Windows."""
        if not MEDIA_AVAILABLE:
            return None
        # NOTE: no try/except here — if the media manager itself fails, that's
        # systemic (e.g. a broken bundle), and it must surface in the Log via
        # read_media_now_playing instead of silently blanking the panel.
        mgr = await MediaManager.request_async()
        session = self._pick_now_playing_session(mgr)
        if session is None:
            self._dbg("smtc", "no session (or no Spotify session in Spotify mode)")
            return None
        aumid = ""
        try:
            aumid = session.source_app_user_model_id or ""
        except Exception:  # noqa: BLE001
            pass
        try:
            props = await session.try_get_media_properties_async()
        except Exception as exc:  # noqa: BLE001
            self._smtc_no_props(aumid, f"reading track info failed: {exc}")
            return None
        title = (props.title or "").strip()
        artist = (props.artist or "").strip()

        is_playing = False
        try:
            info = session.get_playback_info()
            is_playing = int(info.playback_status) == 4  # 4 = PLAYING
        except Exception:  # noqa: BLE001
            pass

        progress_ms = duration_ms = 0
        try:
            tl = session.get_timeline_properties()
            progress_ms = int(tl.position.total_seconds() * 1000)
            span = tl.end_time.total_seconds() - tl.start_time.total_seconds()
            duration_ms = int(max(0, span) * 1000)
        except Exception:  # noqa: BLE001
            pass

        if not title and not artist:
            # A live session with EMPTY metadata (Spotify hides it for music
            # videos, for example). Worth logging — and when it's actually
            # PLAYING, show a generic live card rather than 'not playing'.
            self._smtc_no_props(aumid, "session has no title/artist")
            if not is_playing:
                return None
            app_label = ("Spotify" if "spotify" in aumid.lower()
                         else (aumid or "media app"))
            return {
                "title": f"Playing on {app_label} (no track info)",
                "artist": "", "art_url": None,
                "progress_ms": progress_ms, "duration_ms": duration_ms,
                "is_playing": True, "source": "media", "app": aumid.lower(),
                "fetched_at": int(time.time() * 1000),
            }

        # Only cache a *successful* cover so a transient thumbnail failure can
        # heal on the next read instead of sticking as "no art" forever.
        key = f"{title}|{artist}"
        art_url = self._np_art_cache.get(key)
        if not art_url:
            try:
                art = await self._read_thumbnail(props)
                if art:
                    art_url = "data:image/jpeg;base64," + base64.b64encode(art).decode()
                    self._np_art_cache = {key: art_url}
                else:
                    self._dbg("smtc-art", f"thumbnail empty for {title!r} ({aumid})")
            except Exception as exc:  # noqa: BLE001
                self._dbg("smtc-art", f"thumbnail read error for {title!r}: {exc}")
                art_url = None

        self._dbg("smtc", f"app={aumid} title={title!r} playing={is_playing} "
                          f"dur={duration_ms} art={'yes' if art_url else 'no'}")
        return {
            "title": title or "Unknown", "artist": artist, "art_url": art_url,
            "progress_ms": progress_ms, "duration_ms": duration_ms,
            "is_playing": is_playing, "source": "media",
            "app": aumid.lower(),
            "fetched_at": int(time.time() * 1000),
        }

    @staticmethod
    def _run_smtc(coro, timeout=SMTC_TIMEOUT):
        """Run a WinRT coroutine with a hard timeout so a stuck native call
        can't hang the caller forever. Returns None on timeout/error."""
        async def _guarded():
            return await asyncio.wait_for(coro, timeout)
        return asyncio.run(_guarded())

    def _smtc_no_props(self, aumid, why):
        """Log (throttled) a media session that exists but yields no track
        info — the panel would otherwise just sit on 'not playing' with no
        explanation while hotkey control clearly works."""
        now = time.time()
        if now - getattr(self, "_smtc_noprops_t", 0) > 60:
            self._smtc_noprops_t = now
            self.log(f"[i] media session '{aumid}' found but {why} — the panel "
                     "needs the Spotify Web API for track info (authorize on "
                     "the Spotify tab).")

    def read_media_now_playing(self):
        try:
            return self._run_smtc(self._smtc_snapshot())
        except Exception as exc:  # noqa: BLE001
            # A real failure (not just "nothing playing") — put it in the Log,
            # throttled to once a minute, so a broken media reader is visible
            # instead of the panel just sitting on "not playing".
            now = time.time()
            if now - getattr(self, "_smtc_err_t", 0) > 60:
                self._smtc_err_t = now
                self.log(f"[!] media read failed: {type(exc).__name__}: {exc}")
            return None

    async def _control_active(self, action):
        """Send a transport command to the same session the panel displays."""
        if not MEDIA_AVAILABLE:
            return False
        try:
            mgr = await MediaManager.request_async()
        except Exception:  # noqa: BLE001
            return False
        session = self._pick_now_playing_session(mgr)
        if session is None:
            return False
        for attempt in range(2):
            try:
                if action == "next":
                    await session.try_skip_next_async()
                elif action == "prev":
                    await session.try_skip_previous_async()
                elif action == "playpause":
                    await session.try_toggle_play_pause_async()
                return True
            except Exception:  # noqa: BLE001
                if attempt == 0:
                    await asyncio.sleep(0.2)
                    try:
                        mgr = await MediaManager.request_async()
                        session = self._pick_now_playing_session(mgr) or session
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    return False
        return False

    def transport_active(self, action):
        """Control the currently-displayed media session (Spotify desktop /
        browser) via SMTC — no Premium or Web-API device needed."""
        def go():
            if not MEDIA_AVAILABLE:
                self.notify_text("⚠️ media control needs winsdk "
                                 "(pip install winsdk)", "error")
                return
            ok = self._run_smtc(self._control_active(action))
            if not ok:
                self.log(f"[i] {action}: no controllable media session")
        self._run_async(lambda: self._safe(go, f"transport {action}"))

    def read_spotify_now_playing(self, max_age=2.8):
        """Now-playing via the Spotify Web API (covers remote devices).

        Handles every content type Spotify can play, not just plain tracks:
        podcast episodes (additional_types) get full info, and content the API
        exposes NO details for — music videos, ads, some local files come back
        with item=null while clearly playing — gets a live generic card instead
        of the panel pretending nothing is playing."""
        try:
            pb = self._current_cached(max_age)
        except Exception as exc:  # noqa: BLE001
            # Must be visible (throttled) — a silently failing Web API read
            # looks identical to "nothing playing" and is undebuggable.
            now = time.time()
            if now - getattr(self, "_sp_read_err_t", 0) > 60:
                self._sp_read_err_t = now
                if isinstance(exc, SpotifyNotAuthorized):
                    self.log("[!] Spotify Web API not authorized — click "
                             "Test / Authorize on the Spotify tab to see "
                             "now-playing from Spotify.")
                else:
                    self.log(f"[!] Spotify now-playing read failed: "
                             f"{type(exc).__name__}: {exc}")
            return None
        if not pb:
            self._dbg("spotify", "web api: nothing playing")
            return None
        base = {
            "progress_ms": pb.get("progress_ms") or 0,
            "is_playing": pb.get("is_playing", False),
            "source": "spotify",
            "volume": (pb.get("device") or {}).get("volume_percent"),
            "fetched_at": int(time.time() * 1000),
        }
        item = pb.get("item")
        if item and item.get("type") == "episode":
            show = item.get("show") or {}
            images = item.get("images") or show.get("images") or []
            return dict(base,
                        title=item.get("name"),
                        artist=show.get("name") or show.get("publisher") or "Podcast",
                        art_url=images[0]["url"] if images else None,
                        duration_ms=item.get("duration_ms") or 0)
        if item:
            album = item.get("album", {})
            images = album.get("images", [])
            self._dbg("spotify", f"title={item.get('name')!r} "
                                 f"art={'yes' if images else 'no'} "
                                 f"playing={pb.get('is_playing')}")
            return dict(base,
                        title=item.get("name"),
                        artist=", ".join(a["name"] for a in item.get("artists", [])),
                        art_url=images[0]["url"] if images else None,
                        duration_ms=item.get("duration_ms") or 0)
        # item is null: Spotify is playing something it won't describe.
        if not base["is_playing"] and not base["progress_ms"]:
            return None
        cpt = pb.get("currently_playing_type") or "unknown"
        title = ("Ad break" if cpt == "ad"
                 else "Playing on Spotify (no track info — e.g. a music video)")
        return dict(base, title=title,
                    artist=self._device_name(pb) or "Spotify",
                    art_url=None, duration_ms=0)

    def media_control(self, action, label):
        def go():
            if not MEDIA_AVAILABLE:
                self.notify_text("⚠️ media mode needs winsdk — run: pip install winsdk",
                                 "error")
                return
            result = asyncio.run(self._run_media(action))
            if result is None:
                self.notify_text("⚠️ media: no active session (is something playing "
                                 "in your browser?)", "error")
                return
            title, artist, art = result
            art_url = None
            if art:
                import base64
                art_url = "data:image/jpeg;base64," + base64.b64encode(art).decode()
            self.now_playing = {
                "title": title, "artist": artist, "art_url": art_url,
                "progress_ms": 0, "duration_ms": 0,
                "is_playing": True, "source": "media",
                "fetched_at": int(time.time() * 1000),
            }
            self.notify_media("🎵 Now Playing", title, artist,
                              footer="Media mode (browser / SoundCloud)", art_bytes=art)
        self._run_async(lambda: self._safe(go, label))

    # --------------------------------------------------------- mode dispatch
    def toggle_mode(self):
        if self.mode == "spotify":
            if not MEDIA_AVAILABLE:
                self.notify_text("⚠️ can't switch — media mode needs winsdk "
                                 "(pip install winsdk)", "error")
                return
            self.mode = "media"
            self.notify_text("🔀 Switched to MEDIA mode (browser / SoundCloud)", "media")
        else:
            self.mode = "spotify"
            self.notify_text("🔀 Switched to SPOTIFY mode", "playing")
        if self.on_mode_change:
            try:
                self.on_mode_change(self.mode)
            except Exception:  # noqa: BLE001
                pass

    def on_next(self):
        if self.mode == "media":
            self.media_control("next", "media next")
        else:
            self.sp_next()

    def on_prev(self):
        if self.mode == "media":
            self.media_control("prev", "media prev")
        else:
            self.sp_prev()

    def on_playpause(self):
        if self.mode == "media":
            self.media_control("playpause", "media play/pause")
        else:
            self.sp_playpause()

    def on_add(self):
        if self.mode == "media":
            self.log("[i] add-to-playlist only works in Spotify mode")
        else:
            self.sp_add()

    def on_like(self):
        if self.mode == "media":
            self.log("[i] like only works in Spotify mode")
        else:
            self.sp_like()

    # ------------------------------------------------------------- poller
    def _poller(self):
        interval = max(3, int(self.config["settings"].get("poll_interval", 5)))
        try:
            pb = self._current()
            if pb and pb.get("item"):
                with self._seen_lock:
                    self._seen["track_id"] = pb["item"]["id"]
                    self._seen["is_playing"] = pb.get("is_playing", False)
        except Exception:  # noqa: BLE001
            pass

        while not self._stop_event.is_set():
            self._stop_event.wait(interval)
            if self._stop_event.is_set():
                break
            if self.mode != "spotify":
                continue
            try:
                # Cache window just under the poll interval so the poller and
                # the watcher together produce ~1 API call per interval, not
                # one each (the log showed misses every time at 2.8s vs 3s).
                self._announce_playback(
                    self._current_cached(max(2.5, interval - 0.5)))
            except Exception as exc:  # noqa: BLE001
                if (SPOTIPY_AVAILABLE
                        and isinstance(exc, spotipy.exceptions.SpotifyException)
                        and exc.http_status == 429):
                    self.log("[!] poller rate limited — backing off")
                    self._stop_event.wait(interval * 4)
                else:
                    # The poller is what posts now-playing to Discord — its
                    # failures must be visible (throttled) or the webhook just
                    # "stops working" with an empty log.
                    now = time.time()
                    if now - getattr(self, "_poll_err_t", 0) > 300:
                        self._poll_err_t = now
                        if isinstance(exc, SpotifyNotAuthorized):
                            self.log("[!] Discord now-playing posts are OFF: "
                                     "Spotify isn't authorized — Spotify tab → "
                                     "Test / Authorize.")
                        else:
                            self.log(f"[!] now-playing poller error: "
                                     f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------- lifecycle
    @property
    def running(self):
        return self._running

    def start(self):
        """Register global hotkeys and (optionally) start the now-playing poller."""
        if self._running:
            return
        if not KEYBOARD_AVAILABLE:
            raise RuntimeError(
                "The 'keyboard' library isn't available — run: pip install keyboard "
                "(on Linux it must run as root)."
            )

        actions = {
            "next": self.on_next,
            "prev": self.on_prev,
            "playpause": self.on_playpause,
            "add": self.on_add,
            "like": self.on_like,
            "toggle_mode": self.toggle_mode,
        }
        self._hotkey_handles = []
        for key, action in actions.items():
            combo = (self.config["hotkeys"].get(key) or "").strip()
            if not combo:
                continue
            try:
                handle = keyboard.add_hotkey(combo, action)
                self._hotkey_handles.append(handle)
            except Exception as exc:  # noqa: BLE001
                self.log(f"[!] couldn't register hotkey '{combo}' for {key}: {exc}")

        self._stop_event.clear()
        if self.config["settings"].get("track_activity", True):
            self._poller_thread = threading.Thread(target=self._poller, daemon=True)
            self._poller_thread.start()

        self._running = True
        self.log("Hotkeys active. Engine running.")

    def stop(self):
        """Unregister hotkeys and stop the poller."""
        if not self._running:
            return
        self._stop_event.set()
        for handle in self._hotkey_handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception:  # noqa: BLE001
                pass
        self._hotkey_handles = []
        self._poller_thread = None
        self._running = False
        self.log("Engine stopped. Hotkeys removed.")
