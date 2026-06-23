"""Version history shown in the app's Patch notes dropdown (newest first)."""

CHANGELOG = [
    {
        "version": "1.0.21",
        "notes": [
            "Brought back the separate floating Mini player window (the clean "
            "one), and fixed the freeze: it's now created on the GUI thread (a "
            "window made off-thread looked alive but didn't respond) and fed via "
            "a lightweight now-playing channel.",
        ],
    },
    {
        "version": "1.0.20",
        "notes": [
            "Fixed the Mini player freezing the app — it's now a compact "
            "always-on-top mode of the main window (toggle it from the "
            "now-playing panel) instead of a second window, which avoided the "
            "deadlock. Still floats over borderless games.",
        ],
    },
    {
        "version": "1.0.19",
        "notes": [
            "Spotify volume now syncs both ways: the app uses Spotify's own "
            "volume (Web API), so changing it in either place updates the other "
            "(Spotify Premium required to set volume).",
            "Browser/stream volume still controls the browser's app volume; a "
            "website's in-page player slider can't be moved from outside.",
        ],
    },
    {
        "version": "1.0.18",
        "notes": [
            "Fixed the slow startup introduced with per-app volume — the audio "
            "library (pycaw/comtypes) now loads lazily on first use instead of "
            "at launch, and the volume level is read less often.",
        ],
    },
    {
        "version": "1.0.17",
        "notes": [
            "The volume control now shows the level: a live volume slider with a "
            "percentage in the main panel and the mini player. Drag it to set the "
            "volume, or use −/+ — and you can see the current position.",
        ],
    },
    {
        "version": "1.0.16",
        "notes": [
            "Volume +/- now controls browser / app media properly (per-app "
            "volume via Windows Core Audio), instead of only Spotify or the "
            "whole system. Set the 'Media app hint' (e.g. brave) to target your "
            "browser.",
        ],
    },
    {
        "version": "1.0.15",
        "notes": [
            "Fixed the Mini player hanging when opened a second time — it now "
            "stays alive hidden and just re-shows instead of being recreated.",
        ],
    },
    {
        "version": "1.0.14",
        "notes": [
            "Fixed the '404 / failed to remove temp directory' error after an "
            "update restart — the relaunched exe now extracts its own files "
            "instead of reusing the old one's (cleared the inherited _MEIPASS2).",
        ],
    },
    {
        "version": "1.0.13",
        "notes": [
            "Added volume +/- buttons to the now-playing panel (Spotify volume "
            "when available, otherwise system volume).",
            "Added a Mini player: a small always-on-top overlay with cover art, "
            "skip/prev/play, add-to-playlist, like and volume — stays on top of "
            "borderless games. Open it from the now-playing panel; closing it "
            "leaves the main app running.",
        ],
    },
    {
        "version": "1.0.12",
        "notes": [
            "Reworked exe self-update to be reliable and silent — no more stuck "
            "console window. It renames the running exe aside, drops the new one "
            "in, and relaunches (the way real auto-updaters do).",
        ],
    },
    {
        "version": "1.0.11",
        "notes": [
            "Test build to verify the in-app auto-update — if you can read this "
            "in the app, the update worked. 🎉",
        ],
    },
    {
        "version": "1.0.10",
        "notes": [
            "Fixed exe self-update: it now only updates when there's a genuinely "
            "newer release (no more 'updating' to the same version), and the "
            "exe-swap on restart is far more reliable (waits for full exit, "
            "retries, and logs to mhk_update.log).",
        ],
    },
    {
        "version": "1.0.9",
        "notes": [
            "Faster startup: the now-playing watcher and update check no longer "
            "run during WebView2's cold start, and the .exe bundle is trimmed.",
            "Added a 'Create desktop shortcut' button (General → Updates).",
        ],
    },
    {
        "version": "1.0.8",
        "notes": [
            "The standalone .exe can now self-update: GitHub Actions builds it "
            "on each release and 'Update now' downloads the new .exe and swaps "
            "it in on restart.",
        ],
    },
    {
        "version": "1.0.7",
        "notes": [
            "Fixed a crash dialog on close — the window-close and tray handlers "
            "can no longer throw an unhandled .NET exception.",
        ],
    },
    {
        "version": "1.0.6",
        "notes": [
            "Fixed the Discord webhook getting reset/truncated — the app no "
            "longer falls back to demo data, so a saved webhook always sticks.",
            "Faster startup: persistent WebView2 profile + hide-to-tray right "
            "after load to cut the cold-start delay.",
        ],
    },
    {
        "version": "1.0.5",
        "notes": [
            "Now-playing cover shows the full album art without cropping and "
            "scales with the window.",
            "Added this patch-notes / version history dropdown.",
        ],
    },
    {
        "version": "1.0.4",
        "notes": [
            "Added 'Add to playlist' and 'Like' buttons to the now-playing panel.",
        ],
    },
    {
        "version": "1.0.3",
        "notes": [
            "Fixed the now-playing transport buttons — they now control the track "
            "shown via Windows media, so they work with the Spotify desktop app "
            "without Premium.",
            "Reduced the WebView2 cold-start hang via launch flags.",
        ],
    },
    {
        "version": "1.0.2",
        "notes": [
            "Added a 'Pause webhook posts' toggle on the Discord tab.",
        ],
    },
    {
        "version": "1.0.1",
        "notes": [
            "Fixed a startup freeze by moving startup work off the main UI thread.",
        ],
    },
    {
        "version": "1.0.0",
        "notes": [
            "Initial 'Lo-fi Café' desktop app: global media hotkeys for Spotify "
            "and Windows media, live now-playing panel, Discord now-playing cards, "
            "system tray, self-updater, bundled offline fonts, app icon and a "
            "one-click .exe build.",
        ],
    },
]
