"""
MediaHotKey desktop UI (customtkinter).

A clean, dark settings window where you configure everything the old script
hard-coded in source:

  * Spotify  Client ID / Secret / Redirect URI
  * Discord  webhook URL
  * Hotkeys  every action's key combo (with a "Record" capture button)
  * General  start mode, now-playing polling, media-app hint, launch options

…plus a Start / Stop button for the global hotkeys, a live log, and optional
minimize-to-tray. Settings persist to a per-user config.json.
"""

import queue
import threading
import webbrowser

import customtkinter as ctk
from tkinter import messagebox

from . import __version__, __app_name__
from .config import load_config, save_config, config_path, HOTKEY_LABELS
from .engine import Engine

# Optional: keyboard for "Record" hotkey capture, pystray for the tray icon.
try:
    import keyboard
    _KEYBOARD = True
except Exception:  # noqa: BLE001
    keyboard = None
    _KEYBOARD = False

try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY = True
except Exception:  # noqa: BLE001
    pystray = None
    _TRAY = False

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

SPOTIFY_GREEN = "#1DB954"
ACCENT_HOVER = "#159c44"


class MediaHotKeyApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.config_data = load_config()
        self.engine = None
        self._log_queue = queue.Queue()
        self._tray_icon = None
        self._vars = {}

        self.title(f"{__app_name__} {__version__}")
        self.geometry("760x640")
        self.minsize(720, 600)

        self._build_header()
        self._build_tabs()
        self._build_footer()

        self._load_into_widgets()
        self._poll_log_queue()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if self.config_data["settings"].get("start_engine_on_launch"):
            self.after(400, self._start_engine)
        if _TRAY and self.config_data["settings"].get("start_minimized"):
            self.after(600, self._hide_to_tray)

    # ----------------------------------------------------------- UI: header
    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(16, 4))

        ctk.CTkLabel(
            header, text="🎵  MediaHotKey",
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(side="left")

        self.status_dot = ctk.CTkLabel(
            header, text="● Stopped", text_color="#888",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.status_dot.pack(side="right")

        self.mode_label = ctk.CTkLabel(
            header, text="", text_color="#aaa", font=ctk.CTkFont(size=13),
        )
        self.mode_label.pack(side="right", padx=(0, 14))

    # ------------------------------------------------------------- UI: tabs
    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(self, fg_color=("#f2f2f2", "#1d1d1d"))
        self.tabs.pack(fill="both", expand=True, padx=20, pady=6)
        for name in ("Spotify", "Discord", "Hotkeys", "General", "Log"):
            self.tabs.add(name)

        self._build_spotify_tab(self.tabs.tab("Spotify"))
        self._build_discord_tab(self.tabs.tab("Discord"))
        self._build_hotkeys_tab(self.tabs.tab("Hotkeys"))
        self._build_general_tab(self.tabs.tab("General"))
        self._build_log_tab(self.tabs.tab("Log"))

    def _section(self, parent, text):
        ctk.CTkLabel(
            parent, text=text, font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(16, 2))

    def _hint(self, parent, text):
        ctk.CTkLabel(
            parent, text=text, text_color="#888", justify="left",
            font=ctk.CTkFont(size=12), wraplength=660,
        ).pack(anchor="w", padx=18, pady=(0, 4))

    def _entry_row(self, parent, label, var_key, var, show=None, placeholder=""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(row, text=label, width=140, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(row, textvariable=var, show=show,
                             placeholder_text=placeholder)
        entry.pack(side="left", fill="x", expand=True)
        self._vars[var_key] = var
        return entry

    # -- Spotify tab -------------------------------------------------------
    def _build_spotify_tab(self, tab):
        self._section(tab, "Spotify Web API credentials")
        self._hint(
            tab,
            "Create an app at developer.spotify.com → Dashboard. Copy the "
            "Client ID and Client Secret. In the app's settings add the Redirect "
            "URI below exactly. Playback control (skip/pause) needs Spotify Premium.",
        )

        self._entry_row(tab, "Client ID", "spotify.client_id",
                        ctk.StringVar(), placeholder="from the Spotify dashboard")
        self._entry_row(tab, "Client Secret", "spotify.client_secret",
                        ctk.StringVar(), show="•",
                        placeholder="kept on this machine only")
        self._entry_row(tab, "Redirect URI", "spotify.redirect_uri",
                        ctk.StringVar(),
                        placeholder="http://127.0.0.1:8888/callback")

        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(10, 4))
        ctk.CTkButton(btns, text="Open Spotify Dashboard ↗", width=210,
                      fg_color="#333", hover_color="#444",
                      command=lambda: webbrowser.open(
                          "https://developer.spotify.com/dashboard")).pack(side="left")
        ctk.CTkButton(btns, text="Test / Authorize", width=150,
                      command=self._test_spotify).pack(side="left", padx=8)

        self.spotify_status = ctk.CTkLabel(tab, text="", text_color="#888",
                                           font=ctk.CTkFont(size=12))
        self.spotify_status.pack(anchor="w", padx=18, pady=(2, 8))

    # -- Discord tab -------------------------------------------------------
    def _build_discord_tab(self, tab):
        self._section(tab, "Discord webhook (optional)")
        self._hint(
            tab,
            "Paste a Discord channel webhook URL to post rich now-playing cards, "
            "likes and mode switches. Leave blank to disable Discord entirely. "
            "Create one in a server: Channel → Edit → Integrations → Webhooks.",
        )
        self._entry_row(tab, "Webhook URL", "discord.webhook_url",
                        ctk.StringVar(),
                        placeholder="https://discord.com/api/webhooks/…  (optional)")

        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(10, 4))
        ctk.CTkButton(btns, text="Send test message", width=170,
                      command=self._test_discord).pack(side="left")
        self.discord_status = ctk.CTkLabel(tab, text="", text_color="#888",
                                           font=ctk.CTkFont(size=12))
        self.discord_status.pack(anchor="w", padx=18, pady=(8, 8))

    # -- Hotkeys tab -------------------------------------------------------
    def _build_hotkeys_tab(self, tab):
        self._section(tab, "Global hotkeys")
        self._hint(
            tab,
            "Click Record and press the combination you want (e.g. F9, or "
            "Ctrl+Shift+F9), or type it manually like 'ctrl+alt+f9'. These work "
            "system-wide while the engine is running.",
        )
        for key, label in HOTKEY_LABELS.items():
            row = ctk.CTkFrame(tab, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=4)
            ctk.CTkLabel(row, text=label, width=220, anchor="w").pack(side="left")
            var = ctk.StringVar()
            self._vars[f"hotkeys.{key}"] = var
            ctk.CTkEntry(row, textvariable=var, width=180).pack(side="left")
            ctk.CTkButton(
                row, text="Record", width=80,
                state="normal" if _KEYBOARD else "disabled",
                command=lambda v=var: self._record_hotkey(v),
            ).pack(side="left", padx=8)

        if not _KEYBOARD:
            self._hint(tab, "⚠ The 'keyboard' package isn't installed, so Record "
                            "and the hotkeys themselves won't work. Run: pip install keyboard")

    # -- General tab -------------------------------------------------------
    def _build_general_tab(self, tab):
        self._section(tab, "Mode & playback")
        mode_row = ctk.CTkFrame(tab, fg_color="transparent")
        mode_row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(mode_row, text="Start in mode", width=180, anchor="w").pack(side="left")
        start_mode = ctk.StringVar(value="spotify")
        self._vars["settings.start_mode"] = start_mode
        ctk.CTkSegmentedButton(mode_row, values=["spotify", "media"],
                               variable=start_mode).pack(side="left")

        hint_row = ctk.CTkFrame(tab, fg_color="transparent")
        hint_row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(hint_row, text="Media app hint", width=180, anchor="w").pack(side="left")
        media_hint = ctk.StringVar()
        self._vars["settings.media_app_hint"] = media_hint
        ctk.CTkEntry(hint_row, textvariable=media_hint, width=180,
                     placeholder_text="brave / chrome / msedge").pack(side="left")
        self._hint(tab, "In Media mode, prefer the browser/app whose id contains "
                        "this word. Leave blank to control whatever is current.")

        poll_row = ctk.CTkFrame(tab, fg_color="transparent")
        poll_row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(poll_row, text="Poll interval (s)", width=180, anchor="w").pack(side="left")
        poll = ctk.StringVar(value="5")
        self._vars["settings.poll_interval"] = poll
        ctk.CTkEntry(poll_row, textvariable=poll, width=80).pack(side="left")

        self._section(tab, "Options")
        self._add_switch(tab, "settings.track_activity",
                         "Track now-playing across devices (Spotify mode)")
        self._add_switch(tab, "settings.announce_pause_resume",
                         "Also post when you pause / resume the same track")
        self._add_switch(tab, "settings.start_engine_on_launch",
                         "Start hotkeys automatically when the app opens")
        self._add_switch(tab, "settings.start_minimized",
                         "Launch minimized to the system tray"
                         + ("" if _TRAY else "  (needs pystray + Pillow)"))

        ctk.CTkLabel(tab, text=f"Config file: {config_path()}",
                     text_color="#666", font=ctk.CTkFont(size=11),
                     wraplength=660, justify="left").pack(anchor="w", padx=18, pady=(16, 4))

    def _add_switch(self, parent, var_key, label):
        var = ctk.BooleanVar()
        self._vars[var_key] = var
        ctk.CTkSwitch(parent, text=label, variable=var,
                      progress_color=SPOTIFY_GREEN).pack(anchor="w", padx=18, pady=5)

    # -- Log tab -----------------------------------------------------------
    def _build_log_tab(self, tab):
        self.log_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="monospace", size=12))
        self.log_box.pack(fill="both", expand=True, padx=12, pady=12)
        self.log_box.configure(state="disabled")
        ctk.CTkButton(tab, text="Clear log", width=100,
                      fg_color="#333", hover_color="#444",
                      command=self._clear_log).pack(anchor="e", padx=12, pady=(0, 12))

    # ----------------------------------------------------------- UI: footer
    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(4, 16))

        self.start_btn = ctk.CTkButton(
            footer, text="▶  Start hotkeys", width=180, height=40,
            fg_color=SPOTIFY_GREEN, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._toggle_engine,
        )
        self.start_btn.pack(side="left")

        ctk.CTkButton(footer, text="💾  Save settings", width=150, height=40,
                      fg_color="#333", hover_color="#444",
                      command=self._save).pack(side="left", padx=10)

        caps = Engine.capabilities()
        cap_text = (f"keyboard {'✓' if caps['keyboard'] else '✗'}   "
                    f"spotipy {'✓' if caps['spotipy'] else '✗'}   "
                    f"media/SMTC {'✓' if caps['media'] else '✗'}")
        ctk.CTkLabel(footer, text=cap_text, text_color="#777",
                     font=ctk.CTkFont(size=12)).pack(side="right")

    # ------------------------------------------------------ data <-> widgets
    def _get(self, dotted, default=None):
        node = self.config_data
        for part in dotted.split("."):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        return node if node != {} else default

    def _load_into_widgets(self):
        for dotted, var in self._vars.items():
            value = self._get(dotted)
            if isinstance(var, ctk.BooleanVar):
                var.set(bool(value))
            else:
                var.set("" if value is None else str(value))

    def _collect_from_widgets(self):
        cfg = self.config_data
        for dotted, var in self._vars.items():
            section, key = dotted.split(".", 1)
            value = var.get()
            if dotted == "settings.poll_interval":
                try:
                    value = max(3, int(float(value)))
                except (TypeError, ValueError):
                    value = 5
            cfg.setdefault(section, {})[key] = value
        return cfg

    # ----------------------------------------------------------- actions
    def _save(self):
        self._collect_from_widgets()
        try:
            path = save_config(self.config_data)
            self.log(f"[i] settings saved → {path}")
            self._flash(self.start_btn, "Saved ✓")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("MediaHotKey", f"Couldn't save settings:\n{exc}")

    def _record_hotkey(self, var):
        if not _KEYBOARD:
            return
        var.set("press keys…")
        self.update_idletasks()

        def grab():
            try:
                combo = keyboard.read_hotkey(suppress=False)
            except Exception as exc:  # noqa: BLE001
                combo = ""
                self.log(f"[!] hotkey capture failed: {exc}")
            self.after(0, lambda: var.set(combo or ""))

        threading.Thread(target=grab, daemon=True).start()

    def _test_spotify(self):
        self._collect_from_widgets()
        self.spotify_status.configure(text="Authorizing… a browser tab may open.",
                                      text_color="#aaa")

        def run():
            try:
                eng = Engine(self.config_data, log=self.log)
                pb = eng._current()  # forces OAuth + a real API call
                if pb and pb.get("item"):
                    t = pb["item"]
                    msg = f"✓ Connected — now playing: {t['name']}"
                else:
                    msg = "✓ Connected to Spotify (nothing playing right now)."
                self.after(0, lambda: self.spotify_status.configure(
                    text=msg, text_color=SPOTIFY_GREEN))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: self.spotify_status.configure(
                    text=f"✗ {exc}", text_color="#ED4245"))

        threading.Thread(target=run, daemon=True).start()

    def _test_discord(self):
        self._collect_from_widgets()
        from .discord_notify import Discord
        dc = Discord(self.config_data["discord"].get("webhook_url", ""), log=self.log)
        if not dc.ready():
            self.discord_status.configure(text="✗ No valid webhook URL set.",
                                          text_color="#ED4245")
            return
        dc.text("✅ MediaHotKey test message — your webhook works!", "playing")
        self.discord_status.configure(text="✓ Test message sent. Check your channel.",
                                      text_color=SPOTIFY_GREEN)

    # --------------------------------------------------------- engine control
    def _toggle_engine(self):
        if self.engine and self.engine.running:
            self._stop_engine()
        else:
            self._start_engine()

    def _start_engine(self):
        self._collect_from_widgets()
        save_config(self.config_data)
        try:
            self.engine = Engine(self.config_data, log=self.log,
                                 on_mode_change=self._on_mode_change)
            self.engine.start()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("MediaHotKey", str(exc))
            self.log(f"[!] {exc}")
            return
        self.status_dot.configure(text="● Running", text_color=SPOTIFY_GREEN)
        self.start_btn.configure(text="■  Stop hotkeys", fg_color="#a33",
                                 hover_color="#c44")
        self._on_mode_change(self.engine.mode)

    def _stop_engine(self):
        if self.engine:
            self.engine.stop()
        self.status_dot.configure(text="● Stopped", text_color="#888")
        self.start_btn.configure(text="▶  Start hotkeys", fg_color=SPOTIFY_GREEN,
                                 hover_color=ACCENT_HOVER)
        self.mode_label.configure(text="")

    def _on_mode_change(self, mode):
        self.after(0, lambda: self.mode_label.configure(
            text=f"Mode: {mode.upper()}"))

    # --------------------------------------------------------------- logging
    def log(self, msg):
        self._log_queue.put(str(msg))

    def _poll_log_queue(self):
        drained = []
        try:
            while True:
                drained.append(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        if drained:
            self.log_box.configure(state="normal")
            for line in drained:
                self.log_box.insert("end", line + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(150, self._poll_log_queue)

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _flash(self, button, text):
        old = button.cget("text")
        button.configure(text=text)
        self.after(900, lambda: button.configure(text=old))

    # ------------------------------------------------------------- system tray
    def _hide_to_tray(self):
        if not _TRAY:
            self.iconify()
            return
        self.withdraw()
        if self._tray_icon is None:
            image = Image.new("RGB", (64, 64), "#1DB954")
            draw = ImageDraw.Draw(image)
            draw.polygon([(24, 18), (24, 46), (48, 32)], fill="white")
            menu = pystray.Menu(
                pystray.MenuItem("Open MediaHotKey", self._show_from_tray, default=True),
                pystray.MenuItem(
                    "Start / Stop hotkeys",
                    lambda: self.after(0, self._toggle_engine)),
                pystray.MenuItem("Quit", self._quit_from_tray),
            )
            self._tray_icon = pystray.Icon("MediaHotKey", image, "MediaHotKey", menu)
            threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _show_from_tray(self, *_):
        self.after(0, self.deiconify)
        self.after(0, self.lift)

    def _quit_from_tray(self, *_):
        self.after(0, self._really_quit)

    # ------------------------------------------------------------- shutdown
    def _on_close(self):
        # If tray is available and the engine is running, hide instead of quit
        # so hotkeys keep working in the background.
        if _TRAY and self.engine and self.engine.running:
            self._hide_to_tray()
            self.log("[i] minimized to tray — hotkeys still active. "
                     "Use the tray icon to quit.")
            return
        self._really_quit()

    def _really_quit(self):
        try:
            if self.engine:
                self.engine.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:  # noqa: BLE001
                pass
        self.destroy()


def main():
    app = MediaHotKeyApp()
    app.mainloop()


if __name__ == "__main__":
    main()
