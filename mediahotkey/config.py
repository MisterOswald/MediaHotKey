"""
Configuration storage for MediaHotKey.

Everything that used to live in the constants at the top of the original
script (Spotify keys, Discord webhook, hotkeys, mode settings) now lives in a
JSON file so it can be edited from the UI instead of the source code.

The config file lives in a per-user directory so the app can ship as a single
frozen .exe and still keep settings between runs:

    Windows : %APPDATA%\\MediaHotKey\\config.json
    macOS   : ~/Library/Application Support/MediaHotKey/config.json
    Linux   : ~/.config/MediaHotKey/config.json
"""

import os
import sys
import json
import copy

APP_DIR_NAME = "MediaHotKey"

DEFAULT_CONFIG = {
    "spotify": {
        "client_id": "",
        "client_secret": "",
        "redirect_uri": "http://127.0.0.1:8888/callback",
    },
    "discord": {
        "webhook_url": "",
    },
    "hotkeys": {
        "next": "f9",
        "prev": "shift+f9",
        "playpause": "ctrl+f9",
        "add": "alt+f9",
        "like": "ctrl+alt+f9",
        "toggle_mode": "ctrl+shift+f9",
    },
    "settings": {
        "start_mode": "spotify",          # "spotify" or "media"
        "track_activity": True,           # poll now-playing across devices
        "poll_interval": 5,               # seconds between checks (>= 3)
        "announce_pause_resume": False,   # post on pause/resume of same track
        "media_app_hint": "brave",        # prefer media session matching this
        "start_engine_on_launch": False,  # auto-start hotkeys when UI opens
        "start_minimized": False,         # launch hidden to the system tray
    },
    "mascot": {
        "image": "",                      # user-chosen now-playing panel art (data URL)
    },
}

# Human-readable labels + the action each hotkey maps to. Used by the UI.
HOTKEY_LABELS = {
    "next": "Next track",
    "prev": "Previous track",
    "playpause": "Play / Pause",
    "add": "Add to playlist (Spotify)",
    "like": "Like to library (Spotify)",
    "toggle_mode": "Toggle Spotify / Media mode",
}


def config_dir():
    """Return the per-user config directory, creating it if needed."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(base, APP_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def config_path():
    return os.path.join(config_dir(), "config.json")


def token_cache_path():
    """Where Spotipy caches the OAuth token."""
    return os.path.join(config_dir(), ".spotify_token_cache")


def _deep_merge(base, override):
    """Recursively merge `override` onto a copy of `base`."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config():
    """Load config from disk, filling in any missing keys with defaults."""
    path = config_path()
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULT_CONFIG)
    return _deep_merge(DEFAULT_CONFIG, data)


def save_config(config):
    """Persist config to disk. Returns the path written."""
    path = config_path()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
    return path
