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
        """Local-only debug log (never sent to Discord). De-duplicated per tag
        so a once-per-second poll doesn't flood the Log tab."""
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
    def _ensure_spotify(self):
        """Build the Spotify client on first use (triggers OAuth if needed)."""
        if self._sp is not None:
            return self._sp
        if not SPOTIPY_AVAILABLE:
            raise RuntimeError("spotipy not installed — run: pip install spotipy")
        spec = self.config["spotify"]
        if not spec.get("client_id") or not spec.get("client_secret"):
            raise RuntimeError("Spotify Client ID / Secret not set — open Settings.")
        self._sp = spotipy.Spotify(
            requests_session=_build_session(),
            retries=0,
            auth_manager=SpotifyOAuth(
                client_id=spec["client_id"],
                client_secret=spec["client_secret"],
                redirect_uri=spec.get("redirect_uri", "http://127.0.0.1:8888/callback"),
                scope=SCOPE,
                cache_path=token_cache_path(),
                open_browser=True,
            ),
        )
        return self._sp

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
            sp = self._ensure_spotify()
            sp.next_track()
            time.sleep(0.4)
            self._announce_playback(self._current())
        self._run_async(lambda: self._safe(go, "next track"))

    def sp_prev(self):
        def go():
            sp = self._ensure_spotify()
            sp.previous_track()
            time.sleep(0.4)
            self._announce_playback(self._current())
        self._run_async(lambda: self._safe(go, "previous track"))

    def sp_playpause(self):
        def toggle():
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

    def read_app_volume(self):
        """Current per-app volume as 0-100, or None (for the now-playing panel)."""
        lvl = self._pycaw_volume_op(self._hint(), "get")
        return None if lvl is None else int(round(lvl * 100))

    def _spotify_set_volume(self, percent):
        sp = self._ensure_spotify()
        sp.volume(max(0, min(100, int(percent))))

    def volume(self, delta):
        """Nudge volume by `delta`% — Spotify Web API for a remote Spotify
        track, otherwise the app's own volume, else system media keys."""
        def go():
            src = (self.now_playing or {}).get("source")
            if (src == "spotify" and SPOTIPY_AVAILABLE
                    and self.config["spotify"].get("client_id")
                    and os.path.exists(token_cache_path())):
                try:
                    pb = self._ensure_spotify().current_playback()
                    vol = ((pb or {}).get("device") or {}).get("volume_percent")
                    if vol is not None:
                        self._spotify_set_volume(int(vol) + delta)
                        self.log(f"[ok] Spotify volume {max(0, min(100, int(vol) + delta))}%")
                        return
                except Exception:  # noqa: BLE001
                    pass
            if self._pycaw_volume_op(self._hint(), "add", delta / 100.0) is not None:
                return
            if KEYBOARD_AVAILABLE:
                key = "volume up" if delta > 0 else "volume down"
                for _ in range(max(1, abs(int(delta)) // 4)):
                    try:
                        keyboard.send(key)
                    except Exception:  # noqa: BLE001
                        break
            else:
                self.notify_text("⚠️ volume: no control method available", "error")
        self._run_async(lambda: self._safe(go, "volume"))

    def set_volume(self, percent):
        """Set an absolute volume level (0-100) from the UI slider."""
        percent = max(0, min(100, int(percent)))
        def go():
            src = (self.now_playing or {}).get("source")
            if (src == "spotify" and SPOTIPY_AVAILABLE
                    and self.config["spotify"].get("client_id")
                    and os.path.exists(token_cache_path())):
                try:
                    self._spotify_set_volume(percent)
                    return
                except Exception:  # noqa: BLE001
                    pass
            self._pycaw_volume_op(self._hint(), "set", percent / 100.0)
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
            return None  # no local Spotify session → use the Web API instead

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
        try:
            mgr = await MediaManager.request_async()
        except Exception:  # noqa: BLE001
            return None
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
            self._dbg("smtc", f"media properties failed for {aumid}: {exc}")
            return None
        title = (props.title or "").strip()
        artist = (props.artist or "").strip()
        if not title and not artist:
            return None

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

    def read_media_now_playing(self):
        try:
            return asyncio.run(self._smtc_snapshot())
        except Exception as exc:  # noqa: BLE001
            self._dbg("smtc", f"snapshot crashed: {exc}")
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
            ok = asyncio.run(self._control_active(action))
            if not ok:
                self.log(f"[i] {action}: no controllable media session")
        self._run_async(lambda: self._safe(go, f"transport {action}"))

    def read_spotify_now_playing(self):
        """Now-playing via the Spotify Web API (covers remote devices)."""
        try:
            pb = self._current()
        except Exception as exc:  # noqa: BLE001
            self._dbg("spotify", f"web api error: {exc}")
            return None
        if not pb or not pb.get("item"):
            self._dbg("spotify", "web api: nothing playing")
            return None
        track = pb["item"]
        album = track.get("album", {})
        images = album.get("images", [])
        self._dbg("spotify", f"title={track.get('name')!r} "
                             f"art={'yes' if images else 'no'} "
                             f"playing={pb.get('is_playing')}")
        return {
            "title": track.get("name"),
            "artist": ", ".join(a["name"] for a in track.get("artists", [])),
            "art_url": images[0]["url"] if images else None,
            "progress_ms": pb.get("progress_ms") or 0,
            "duration_ms": track.get("duration_ms") or 0,
            "is_playing": pb.get("is_playing", False),
            "source": "spotify",
            "volume": (pb.get("device") or {}).get("volume_percent"),
            "fetched_at": int(time.time() * 1000),
        }

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
                self._announce_playback(self._current())
            except Exception as exc:  # noqa: BLE001
                if (SPOTIPY_AVAILABLE
                        and isinstance(exc, spotipy.exceptions.SpotifyException)
                        and exc.http_status == 429):
                    self.log("[!] poller rate limited — backing off")
                    self._stop_event.wait(interval * 4)

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
