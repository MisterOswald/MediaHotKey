# 🎵 MediaHotKey

Global media hotkeys for **Spotify** *and* anything else playing on your PC
(SoundCloud, YouTube, browser audio…), wrapped in a warm **"lo-fi café"**
desktop app.

Press one key to skip / pause / like / add-to-playlist, get rich **Discord
now-playing cards**, and control browser audio **without injecting keystrokes**
(anti-cheat / Vanguard-safe).

![mode badge](https://img.shields.io/badge/mode-Spotify%20%7C%20Media-CC7E4F)
![ui badge](https://img.shields.io/badge/UI-lo--fi%20caf%C3%A9-E0B254)

---

## The app

A cozy, light-mode interface (terracotta + sage palette, Zen Maru Gothic / JetBrains
Mono) with five tabs and a persistent **now-playing panel**:

- **Spotify** — Client ID / Secret / Redirect URI + a *Test / Authorize* button
- **Discord** — webhook URL + a *Send test message* button
- **Hotkeys** — every action rebindable, with a **Record** capture button
- **General** — start mode, media-app hint, poll interval, and four toggles
- **Log** — live activity feed

…plus a **Start / Stop hotkeys** button, a pulsing *Running* pill, live capability
checks (`keyboard ✓  spotify ✓  media/SMTC ✓`), and a now-playing card with album
art, progress and transport controls.

The UI is HTML/CSS/JS rendered in a native window via **pywebview**; the Python
**engine** does the actual hotkey / Spotify / media work behind a small JS bridge.
The Zen Maru Gothic / JetBrains Mono fonts are **bundled locally** (in
`mediahotkey/web/fonts/`), so the app looks right with **no internet connection**.

> 👀 **Want to see the design first?** Just open `mediahotkey/web/index.html` in
> any browser — it renders with demo data, no Python required.

---

## Two modes (toggle with a hotkey)

- **Spotify mode** *(default)* — full control through the Spotify Web API:
  skip / prev / play-pause / **like** / **add-to-current-playlist**, Discord
  embeds, and cross-device now-playing tracking. *Playback control requires
  Spotify Premium.*
- **Media mode** — universal transport control via Windows **System Media
  Transport Controls (SMTC)**. Controls whatever is playing in your browser/app.
  Transport only (no like/add). *Windows 10/11 + `winsdk`.*

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

> **No black cmd window?** `python run.py` shows a console because `python.exe`
> is a console program. Double-click **`MediaHotKey.pyw`** instead (uses the
> windowless `pythonw`). The built `.exe` is windowless too.

Then in the window:

1. **Spotify tab** — paste your Client ID / Secret (and add the Redirect URI to
   your Spotify app). Click **Test / Authorize** — a browser tab opens once.
2. **Discord tab** *(optional)* — paste a webhook URL, hit **Send test message**.
3. **Hotkeys tab** — keep the defaults or click **Record** and press your own.
4. Press **▶ Start hotkeys**.

### Getting Spotify credentials

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. **Create app** → copy the **Client ID** and **Client Secret**.
3. In the app's **Settings → Redirect URIs**, add exactly:
   `http://127.0.0.1:8888/callback`

### Getting a Discord webhook

Server → **Channel → Edit → Integrations → Webhooks → New Webhook → Copy URL**.

---

## Build a standalone `.exe` (Windows)

No Python needed on the target machine — and it carries the app icon:

```bat
build_exe.bat
```

Produces **`dist\MediaHotKey.exe`**. Settings live in
`%APPDATA%\MediaHotKey\config.json`, so the exe stays portable.

> Windows 10/11 already ship the **Edge WebView2** runtime pywebview needs. On a
> rare machine without it, install "WebView2 Runtime" from Microsoft (free).

To regenerate the icon after tweaking it: `python assets/make_icon.py`.

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
  (SMTC is Windows-only). pywebview uses the system WebView (WebKit/GTK/Qt); on
  Linux the global hotkeys via `keyboard` require running as root.
- The app detects missing optional libraries and shows their status in the
  footer.

---

## Project layout

```
MediaHotKey/
├── MediaHotKey.pyw        # double-click to launch with NO console window
├── run.py                 # launcher (UI, or --headless)
├── build_exe.bat          # one-click PyInstaller build → dist/MediaHotKey.exe
├── requirements.txt
├── assets/
│   ├── icon.ico           # app / window / exe icon
│   ├── make_icon.py       # regenerates the icon + web logo
│   └── fetch_fonts.py     # re-downloads + bundles the UI fonts (offline)
└── mediahotkey/
    ├── config.py          # load/save per-user config.json + defaults
    ├── discord_notify.py  # Discord webhook embeds
    ├── engine.py          # hotkeys + Spotify + SMTC media control (start/stop)
    ├── gui.py             # pywebview window + JS↔Python bridge (Api)
    └── web/               # the "lo-fi café" UI
        ├── index.html
        ├── styles.css
        ├── app.js
        ├── logo.png
        ├── fonts.css      # @font-face rules → local woff2
        └── fonts/         # bundled Zen Maru Gothic + JetBrains Mono
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
- **Blank window** — install/repair the Microsoft **WebView2 Runtime**.
