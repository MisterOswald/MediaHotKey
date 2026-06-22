"""Version history shown in the app's Patch notes dropdown (newest first)."""

CHANGELOG = [
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
