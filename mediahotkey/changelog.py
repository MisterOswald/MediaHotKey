"""Version history shown in the app's Patch notes dropdown (newest first)."""

CHANGELOG = [
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
