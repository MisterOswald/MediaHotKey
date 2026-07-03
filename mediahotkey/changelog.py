"""Version history shown in the app's Patch notes dropdown (newest first)."""

CHANGELOG = [
    {
        "version": "1.0.37",
        "notes": [
            "Discord posts going quiet is no longer silent: the background "
            "poster now logs WHY it can't post (most commonly 'Spotify isn't "
            "authorized — Test / Authorize') instead of failing invisibly "
            "every few seconds.",
            "Volume buttons/slider no longer fail silently either: every "
            "volume change logs what it did ('[ok] Spotify volume 45%' / "
            "'[ok] app volume 60%') or exactly why it couldn't — including "
            "when Spotify's playback device refuses app volume control, and "
            "when no app audio session matches the Media app hint.",
        ],
    },
    {
        "version": "1.0.36",
        "notes": [
            "Fixed everything Spotify dying mid-session (stale panel, skip "
            "hotkey doing nothing, Discord posts stopping) with 'input(): "
            "lost sys.stdin' in the Log: when the sign-in token expired and "
            "its refresh failed, the Spotify library tried to ask for console "
            "input — impossible in a windowed app — and every Spotify call "
            "crashed until restart. It can no longer prompt: an expired/"
            "revoked sign-in now shows a clear 'click Test / Authorize' "
            "message in the Log and recovers the moment you re-authorize.",
        ],
    },
    {
        "version": "1.0.35",
        "notes": [
            "Now-playing works with the Spotify WEB PLAYER (open.spotify.com "
            "in a browser): in Spotify mode the panel only accepted the "
            "desktop app's media session, so browser playback showed as 'not "
            "playing'. An actively playing browser session now shows too "
            "(paused background tabs still can't hijack the card).",
            "No more invisible failures anywhere in now-playing: Spotify Web "
            "API read errors (including 'not authorized') now appear in the "
            "Log, the watcher recovers from any internal error instead of "
            "silently dying, and when there's simply no source to read the "
            "Log says so.",
        ],
    },
    {
        "version": "1.0.34",
        "notes": [
            "Fixed 'not playing' while Spotify plays a MUSIC VIDEO (or an ad): "
            "Spotify exposes no track details for those, and the panel treated "
            "'no details' as 'nothing playing'. It now shows a live 'Playing "
            "on Spotify (no track info)' card with the play state — and "
            "regular songs keep showing full title/art as before.",
            "Podcast episodes now show properly in the panel too (episode "
            "name, show name and cover) instead of appearing as nothing.",
        ],
    },
    {
        "version": "1.0.33",
        "notes": [
            "Fixed the now-playing panel freezing on the previous song (stuck "
            "time/cover) once an overlay had been opened: sending updates into "
            "a hidden/suspended overlay window could hang the whole "
            "now-playing watcher. Updates now go out on a side thread that can "
            "never block the watcher, and hidden overlays stop receiving them "
            "entirely.",
            "Fixed the Mini player / Taskbar bar showing 'not playing' while "
            "the main panel worked: overlay update failures are now logged "
            "(instead of vanishing), and the overlays self-heal — if pushed "
            "updates stop arriving for ~10s they quietly fetch now-playing "
            "themselves at a slow, safe pace.",
        ],
    },
    {
        "version": "1.0.32",
        "notes": [
            "Rebuilt the Spotify sign-in so it can no longer hang silently on "
            "'Authorizing…'. The app now runs the approval step itself: it "
            "logs the sign-in link (copy it from the Log tab into any browser "
            "if no tab opens), waits up to 2 minutes, shows a clear ✅/✗ page "
            "in the browser tab, and reports exactly what failed — including "
            "Spotify's own error when the Redirect URI is wrong.",
            "When Windows reports a music session without any track info "
            "(hotkeys control it, but some Spotify builds publish no "
            "title/artist), the Log now explains that the panel needs the "
            "Spotify Web API — instead of silently showing 'not playing'.",
        ],
    },
    {
        "version": "1.0.31",
        "notes": [
            "Fixed the now-playing panel staying on 'not playing' while music "
            "was clearly playing: the 1.0.29 exe slimming broke the "
            "Windows-media reader at runtime (its components load dynamically, "
            "so the import checks still passed). The full media components are "
            "back, and media-read failures now show in the Log instead of "
            "being silently swallowed.",
            "Test / Authorize now verifies your Client ID / Secret pair with "
            "Spotify in ~a second BEFORE opening the browser sign-in — a "
            "wrong or regenerated pair gets a clear error instead of hanging "
            "on 'Authorizing…' forever. Also added an on-screen hint for the "
            "'Invalid redirect URI' case.",
        ],
    },
    {
        "version": "1.0.30",
        "notes": [
            "Fixed Spotify stuck on 'authorizing…' with a blank now-playing "
            "panel. When the saved sign-in could no longer be refreshed (e.g. "
            "after regenerating the client secret), background code silently "
            "fell into an interactive browser sign-in and blocked forever — "
            "jamming the now-playing watcher and squatting the sign-in port so "
            "a real Test / Authorize could never finish. Background code now "
            "never signs in interactively; only Test / Authorize does, it "
            "clears a dead saved sign-in first, and it tells you if the "
            "sign-in port is blocked by a stuck old MediaHotKey process.",
        ],
    },
    {
        "version": "1.0.29",
        "notes": [
            "Much faster startup: the .exe no longer bundles the entire "
            "Windows SDK — only the two pieces the app actually uses. The "
            "one-file exe unpacks itself (and gets antivirus-scanned) on every "
            "launch, and that payload was most of the wait.",
            "More launch trims: the system-tray library now loads on first "
            "use instead of at startup, and update-leftover cleanup runs "
            "after the window is up instead of before it.",
            "The Log tab now shows a '[start] …' line breaking down where "
            "launch time went (exe unpack / imports / window+page) — if it's "
            "ever slow again, that line says exactly why.",
        ],
    },
    {
        "version": "1.0.28",
        "notes": [
            "Really fixed the overlay freeze this time — the Mini player and "
            "Taskbar bar no longer call back into the app every second. Two "
            "windows both polling the shared bridge is what deadlocked it "
            "('Not Responding'). The main app now pushes now-playing into the "
            "overlays instead, so they update live without ever wedging the "
            "window. Buttons still work as before.",
        ],
    },
    {
        "version": "1.0.27",
        "notes": [
            "Fixed the app going 'Not Responding' after opening the Taskbar "
            "bar (and the Mini player) — a background call the overlay windows "
            "made every second was passing an argument across the bridge, "
            "which wedged it. The overlays now use the original arg-less feed, "
            "so both float smoothly again without freezing the main window.",
        ],
    },
    {
        "version": "1.0.26",
        "notes": [
            "Added a second, tiny 'Taskbar bar' player — a slim horizontal "
            "strip (cover, title, prev/play/next) you can drag to hover over "
            "the Windows taskbar while gaming in windowed mode. Open it from "
            "the now-playing panel, next to the full Mini player.",
        ],
    },
    {
        "version": "1.0.25",
        "notes": [
            "Fixed now-playing randomly not updating for a while then catching "
            "up — the Spotify Web API and Windows-media reads now have hard "
            "timeouts, so a slow/stalled network call can no longer freeze the "
            "now-playing watcher until it eventually returns.",
            "Fixed the window randomly stuttering / going unresponsive — the "
            "now-playing data (including the cover image) is now only sent to "
            "the UI when it actually changes instead of every second, cutting "
            "the bridge traffic that caused the hitching.",
        ],
    },
    {
        "version": "1.0.24",
        "notes": [
            "Hotkeys and skip/play-pause are instant again — the app now "
            "controls the local Spotify desktop app via Windows media first "
            "instead of trying the slow Spotify Web API, which removes the long "
            "delay and the 'no active device / nothing playing' error.",
        ],
    },
    {
        "version": "1.0.23",
        "notes": [
            "Fixed lag that built up over time — stopped streaming the whole "
            "activity log across the UI every second (it's now fetched only when "
            "the Log tab is open) and turned off the noisy debug logging.",
            "Fixed 'no active Spotify device' on skip/play-pause: it now falls "
            "back to controlling the Spotify desktop app directly via Windows "
            "media when the Web API has no active device.",
        ],
    },
    {
        "version": "1.0.22",
        "notes": [
            "Spotify volume now always uses Spotify's own volume (Web API) — "
            "never the Windows mixer — so the app's slider matches Spotify's "
            "exactly (requires authorizing on the Spotify tab; Premium needed "
            "to set). Volume changes also reflect instantly in the UI.",
        ],
    },
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
