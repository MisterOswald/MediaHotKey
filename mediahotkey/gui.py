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
import base64
import threading
import collections

try:
    import webview
except Exception:  # noqa: BLE001
    webview = None

from . import __version__
from .config import load_config, save_config, config_path, token_cache_path
from .engine import Engine, KEYBOARD_AVAILABLE, MEDIA_AVAILABLE
from .discord_notify import Discord

if KEYBOARD_AVAILABLE:
    import keyboard

# Optional system-tray support (keeps hotkeys alive after closing the window).
try:
    import pystray
    from PIL import Image
    _TRAY = True
except Exception:  # noqa: BLE001
    pystray = None
    _TRAY = False


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
        self.window = None
        self._allow_close = False
        self._tray = None
        self.engine = Engine(self.config, log=self._log,
                             on_mode_change=lambda m: None)

        # Always-on now-playing watcher (independent of the hotkey engine).
        self._np_stop = threading.Event()
        self._np_last_spotify = None
        self._np_last_spotify_t = 0.0
        self._np_misses = 0
        self._np_thread = threading.Thread(target=self._np_loop, daemon=True)
        self._np_thread.start()

    # -- logging ----------------------------------------------------------
    def _log(self, msg):
        with self._log_lock:
            self.logs.append(str(msg))

    # -- now-playing watcher ---------------------------------------------
    def _np_loop(self):
        """Continuously refresh engine.now_playing from whichever source is
        live: Windows SMTC first (Spotify desktop + browser), then the Spotify
        Web API (covers playback on remote devices)."""
        while not self._np_stop.is_set():
            np = None
            if MEDIA_AVAILABLE:
                np = self.engine.read_media_now_playing()
            now = time.time()
            spec = self.config.get("spotify", {})
            # Only hit the Web API if the user has already authorized once
            # (token cache present) — never trigger an interactive login here.
            if (np is None and spec.get("client_id") and spec.get("client_secret")
                    and os.path.exists(token_cache_path())):
                # Throttle Web API calls (~ every 4s) and reuse between checks.
                if now - self._np_last_spotify_t >= 4:
                    self._np_last_spotify_t = now
                    self._np_last_spotify = self.engine.read_spotify_now_playing()
                np = self._np_last_spotify
            if np:
                np["fetched_at"] = int(now * 1000)
                self.engine.now_playing = np
                self._np_misses = 0
            else:
                # Don't blank on a single transient miss — hold the last track
                # (paused) and only clear after several seconds of nothing.
                self._np_misses += 1
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
            self._np_stop.wait(1.0)

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

    def get_state(self):
        return {
            "config": self.config,
            "caps": Engine.capabilities(),
            "running": self.engine.running,
            "mode": self.engine.mode,
            "config_path": config_path(),
            "version": __version__,
        }

    def poll(self):
        with self._log_lock:
            logs = list(self.logs)
        return {
            "running": self.engine.running,
            "mode": self.engine.mode,
            "caps": Engine.capabilities(),
            "now_playing": self.engine.now_playing,
            "logs": logs,
        }

    def save_config(self, cfg):
        self._apply(cfg)
        return {"ok": True}

    def clear_log(self):
        with self._log_lock:
            self.logs.clear()
        return {"ok": True}

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
        try:
            if action == "next":
                self.engine.on_next()
            elif action == "prev":
                self.engine.on_prev()
            elif action == "playpause":
                self.engine.on_playpause()
        except Exception as exc:  # noqa: BLE001
            self._log(f"[!] transport {action}: {exc}")
        return {"ok": True}

    # -- tests ------------------------------------------------------------
    def test_spotify(self, cfg=None):
        self._apply(cfg)
        try:
            probe = Engine(self.config, log=self._log)
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
        close in pywebview. Without tray support, quit normally so the app
        can't get stranded with no window."""
        if self._allow_close:
            return True
        if not _TRAY:
            self._quit_app()
            return True
        self._hide_to_tray()
        return False

    def _hide_to_tray(self):
        try:
            self.window.hide()
        except Exception:  # noqa: BLE001
            pass
        self._ensure_tray()
        self._log("[i] minimized to tray — hotkeys still active. "
                  "Use the tray icon to reopen or quit.")

    def show_window(self):
        try:
            self.window.show()
        except Exception:  # noqa: BLE001
            pass

    def _ensure_tray(self):
        if not _TRAY or self._tray is not None:
            return
        icon_img = None
        path = _icon_path()
        if path:
            try:
                icon_img = Image.open(path)
            except Exception:  # noqa: BLE001
                icon_img = None
        if icon_img is None:
            icon_img = Image.new("RGB", (64, 64), "#CC7E4F")
        menu = pystray.Menu(
            pystray.MenuItem("Open MediaHotKey",
                             lambda icon, item: self.show_window(), default=True),
            pystray.MenuItem("Start / Stop hotkeys",
                             lambda icon, item: self.toggle_engine(None)),
            pystray.MenuItem("Quit", lambda icon, item: self._quit_app()),
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

    api = Api()
    index = os.path.join(_resource_dir(), "index.html")
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

    # Closing the X hides to the tray (hotkeys keep running) instead of quitting.
    try:
        window.events.closing += api.on_closing
    except Exception:  # noqa: BLE001
        pass

    settings = api.config["settings"]
    if settings.get("start_engine_on_launch"):
        def _autostart():
            try:
                api.engine.start()
            except Exception as exc:  # noqa: BLE001
                api._log(f"[!] {exc}")
        threading.Timer(1.0, _autostart).start()

    if _TRAY and settings.get("start_minimized"):
        threading.Timer(0.8, api._hide_to_tray).start()

    # Force the modern Chromium (WebView2) backend on Windows so the UI's
    # modern JS runs; fall back gracefully on other platforms / old pywebview.
    gui = "edgechromium" if sys.platform.startswith("win") else None
    try:
        webview.start(gui=gui) if gui else webview.start()
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
