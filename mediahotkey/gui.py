"""
MediaHotKey desktop app — "Lo-fi Café" UI.

The interface is HTML/CSS/JS (see mediahotkey/web/) rendered in a native
window via pywebview; this module is the Python side: it serves the window,
bridges JavaScript ↔ the Engine through the `Api` class, and streams the log
and now-playing state back to the UI.
"""

import os
import sys
import base64
import threading
import collections

try:
    import webview
except Exception:  # noqa: BLE001
    webview = None

from . import __version__
from .config import load_config, save_config, config_path
from .engine import Engine, KEYBOARD_AVAILABLE
from .discord_notify import Discord

if KEYBOARD_AVAILABLE:
    import keyboard


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
        self._maximized = False
        self.engine = Engine(self.config, log=self._log,
                             on_mode_change=lambda m: None)

    # -- logging ----------------------------------------------------------
    def _log(self, msg):
        with self._log_lock:
            self.logs.append(str(msg))

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
        # keep the engine pointed at the same (now-updated) config object
        self.engine.config = self.config

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

    # -- window controls (frameless custom chrome) -----------------------
    def minimize(self):
        try:
            self.window.minimize()
        except Exception:  # noqa: BLE001
            pass

    def toggle_maximize(self):
        try:
            if self._maximized:
                self.window.restore()
            else:
                self.window.maximize()
            self._maximized = not self._maximized
        except Exception:  # noqa: BLE001
            pass

    def close(self):
        try:
            if self.engine.running:
                self.engine.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.window.destroy()
        except Exception:  # noqa: BLE001
            pass


def main():
    if webview is None:
        print("pywebview is required. Install dependencies with:\n"
              "    pip install -r requirements.txt")
        sys.exit(1)

    api = Api()
    index = os.path.join(_resource_dir(), "index.html")
    window = webview.create_window(
        f"MediaHotKey {__version__}",
        url=index,
        js_api=api,
        width=1100,
        height=820,
        min_size=(960, 720),
        frameless=True,
        easy_drag=False,
        background_color="#F6EFE1",
    )
    api.window = window

    if api.config["settings"].get("start_engine_on_launch"):
        def _autostart():
            try:
                api.engine.start()
            except Exception as exc:  # noqa: BLE001
                api._log(f"[!] {exc}")
        threading.Timer(1.0, _autostart).start()

    start_kwargs = {}
    icon = _icon_path()
    if icon:
        start_kwargs["icon"] = icon
    try:
        webview.start(**start_kwargs)
    except TypeError:
        # older pywebview without icon kwarg
        webview.start()


if __name__ == "__main__":
    main()
