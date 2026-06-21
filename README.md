# 🎵 MediaHotKey

Global media hotkeys for **Spotify** *and* anything else playing on your PC
(SoundCloud, YouTube, browser audio…) — now a real desktop app with a clean
configuration UI instead of a script you edit by hand.

Press one key to skip / pause / like / add-to-playlist, get rich **Discord
now-playing cards**, and control browser audio **without injecting keystrokes**
(anti-cheat / Vanguard-safe).

![mode badge](https://img.shields.io/badge/mode-Spotify%20%7C%20Media-1DB954)

---

## What's new vs. the original script

The old `audiohotkey.py` made you paste your Spotify keys, Discord webhook and
hotkeys directly into the source. MediaHotKey wraps all of that in a proper app:

| Old way | New way |
| --- | --- |
| Edit constants in the `.py` file | **Settings UI** with tabs |
| Keys hard-coded | Stored in a per-user `config.json` |
| Fixed F9 hotkeys | **Rebind any hotkey** (with a Record button) |
| Run a terminal, leave it open | **Start/Stop button** + minimize to system tray |
| `print()` to a console | Live **Log** tab |
| Copy the script around | Build a single **`MediaHotKey.exe`** |

---

## Two modes (toggle with a hotkey)

- **Spotify mode** *(default)* — full control through the Spotify Web API:
  skip / prev / play-pause / **like** / **add-to-current-playlist**, Discord
  embeds, and cross-device now-playing tracking (phone, desktop…).
  *Playback control requires Spotify Premium.*
- **Media mode** — universal transport control via Windows **System Media
  Transport Controls (SMTC)**. Controls whatever is currently playing in your
  browser/app. Transport only (no like/add — those are Spotify-only).
  *Windows 10/11 + `winsdk`.*

### Default hotkeys (all rebindable)

| Hotkey | Action |
| --- | --- |
| `F9` | Next track |
| `Shift+F9` | Previous track |
| `Ctrl+F9` | Play / Pause |
| `Alt+F9` | Add to current playlist *(Spotify)* |
| `Ctrl+Alt+F9` | Like to library *(Spotify)* |
| `Ctrl+Shift+F9` | Toggle Spotify ⇄ Media mode |

---

## Quick start (run from source)

```bash
# 1. install dependencies
pip install -r requirements.txt

# 2. launch the app
python run.py
```

Then in the window:

1. **Spotify tab** — paste your Client ID / Secret (and add the Redirect URI to
   your Spotify app). Click **Test / Authorize** — a browser tab opens once to
   sign in.
2. **Discord tab** *(optional)* — paste a channel webhook URL, hit **Send test
   message**.
3. **Hotkeys tab** — keep the defaults or click **Record** and press your own.
4. Press **▶ Start hotkeys**. That's it — the keys now work system-wide.

> Tip: enable *"Start hotkeys automatically"* and *"Launch minimized to tray"*
> in **General** so it just runs quietly in the background on launch.

### Getting Spotify credentials

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. **Create app** → copy the **Client ID** and **Client Secret**.
3. In the app's **Settings → Redirect URIs**, add exactly:
   `http://127.0.0.1:8888/callback`

### Getting a Discord webhook

Server → **Channel → Edit → Integrations → Webhooks → New Webhook → Copy URL**.

---

## Build a standalone `.exe` (Windows)

No Python needed on the target machine:

```bat
build_exe.bat
```

This produces **`dist\MediaHotKey.exe`** — double-click to run. Your settings
live in `%APPDATA%\MediaHotKey\config.json`, so the exe stays portable.

---

## Command-line / headless

Already configured and just want it running with no window?

```bash
python run.py --headless
```

---

## Where things are stored

| What | Location |
| --- | --- |
| Settings | `%APPDATA%\MediaHotKey\config.json` (Win) · `~/.config/MediaHotKey/` (Linux) · `~/Library/Application Support/MediaHotKey/` (macOS) |
| Spotify token cache | next to the config, as `.spotify_token_cache` |

Your Client Secret and tokens never leave your machine.

---

## Platform notes

- **Windows 10/11** — full support, including Media mode (install `winsdk`).
- **macOS / Linux** — Spotify mode and the UI work; **Media mode is disabled**
  (SMTC is Windows-only). On Linux the global hotkeys via `keyboard` require
  running as root.
- The app detects missing optional libraries and shows their status in the
  footer (`keyboard ✓  spotipy ✓  media/SMTC ✓`).

---

## Project layout

```
MediaHotKey/
├── run.py                 # launcher (UI, or --headless)
├── build_exe.bat          # one-click PyInstaller build → dist/MediaHotKey.exe
├── requirements.txt
└── mediahotkey/
    ├── config.py          # load/save per-user config.json + defaults
    ├── discord_notify.py  # Discord webhook embeds
    ├── engine.py          # hotkeys + Spotify + SMTC media control (start/stop)
    └── gui.py             # customtkinter settings UI
```

---

## Troubleshooting

- **"no active Spotify device"** — press play in Spotify once so it has an
  active device, then use the hotkeys.
- **"forbidden — requires Premium"** — Spotify only allows playback control on
  Premium accounts.
- **Hotkeys do nothing** — make sure you pressed **Start hotkeys**, and on
  Windows try running as Administrator (some games capture keys at a lower level).
- **Media mode says "needs winsdk"** — `pip install winsdk` (Windows only).
