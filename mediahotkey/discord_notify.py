"""
Discord webhook helpers.

Posts rich embeds (now-playing cards, like/add confirmations, status text)
to a Discord webhook. All network calls run on daemon threads so they never
block a hotkey press. Failures are logged but never raised.
"""

import threading
from json import dumps as json_dumps

import requests

COLORS = {
    "playing": 0x1DB954,   # Spotify green
    "pause": 0x535353,     # grey
    "add": 0x1DB954,
    "like": 0xE91E63,      # pink / heart
    "media": 0xFF5500,     # SoundCloud orange
    "info": 0x5865F2,      # discord blurple
    "error": 0xED4245,     # red
}


class Discord:
    """Thin wrapper around a single webhook URL."""

    def __init__(self, webhook_url="", log=print):
        self.webhook_url = (webhook_url or "").strip()
        self.log = log

    def ready(self):
        url = self.webhook_url
        return bool(url) and url.startswith("http") and "XXXX" not in url

    # -- low level ---------------------------------------------------------
    def _post(self, payload):
        if not self.ready():
            return

        def post():
            try:
                requests.post(self.webhook_url, json=payload, timeout=5)
            except Exception as exc:  # noqa: BLE001 - best effort
                self.log(f"[!] discord: {exc}")

        threading.Thread(target=post, daemon=True).start()

    def _post_with_image(self, payload, image_bytes, filename="cover.jpg"):
        """Post an embed plus an inline image as a multipart attachment.
        The embed should reference it as thumbnail url 'attachment://<file>'."""
        if not self.ready():
            return

        def post():
            try:
                files = {
                    "payload_json": (None, json_dumps(payload), "application/json"),
                    "files[0]": (filename, image_bytes, "image/jpeg"),
                }
                requests.post(self.webhook_url, files=files, timeout=10)
            except Exception as exc:  # noqa: BLE001 - best effort
                self.log(f"[!] discord: {exc}")

        threading.Thread(target=post, daemon=True).start()

    # -- high level --------------------------------------------------------
    def text(self, msg, color_key="info"):
        self._post({"embeds": [{
            "description": msg,
            "color": COLORS.get(color_key, COLORS["info"]),
        }]})

    def track(self, action_label, track, color_key="playing", footer=None):
        name = track["name"]
        artists = ", ".join(a["name"] for a in track["artists"])
        album = track.get("album", {})
        images = album.get("images", [])
        art_url = images[0]["url"] if images else None
        track_url = track.get("external_urls", {}).get("spotify")

        embed = {
            "author": {"name": action_label},
            "title": name,
            "description": f"by **{artists}**",
            "color": COLORS.get(color_key, COLORS["info"]),
        }
        if track_url:
            embed["url"] = track_url
        if art_url:
            embed["thumbnail"] = {"url": art_url}
        if album.get("name"):
            embed.setdefault("fields", []).append(
                {"name": "Album", "value": album["name"], "inline": True}
            )
        if footer:
            embed["footer"] = {"text": footer}
        self._post({"embeds": [embed]})

    def media(self, action_label, title, artist, footer=None, art_bytes=None):
        line = f"**{title}**" + (f"\nby **{artist}**" if artist else "")
        embed = {
            "author": {"name": action_label},
            "description": line,
            "color": COLORS["media"],
        }
        if footer:
            embed["footer"] = {"text": footer}
        if art_bytes:
            embed["thumbnail"] = {"url": "attachment://cover.jpg"}
            self._post_with_image({"embeds": [embed]}, art_bytes)
        else:
            self._post({"embeds": [embed]})
