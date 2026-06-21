"""
Generate MediaHotKey's app icon — the warm terracotta "lo-fi café" tile with
a white music note, matching the UI logo (gradient #E0B254 → #CC7E4F).

Run:  python assets/make_icon.py
Outputs:  assets/icon.ico (multi-size)  and  mediahotkey/web/logo.png
"""

import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

TOP = (224, 178, 84)      # #E0B254
BOTTOM = (204, 126, 79)   # #CC7E4F


def _rounded_mask(size, radius):
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def _gradient(size):
    """Diagonal-ish vertical gradient TOP→BOTTOM."""
    grad = Image.new("RGB", (size, size), TOP)
    px = grad.load()
    for y in range(size):
        t = y / max(1, size - 1)
        r = int(TOP[0] + (BOTTOM[0] - TOP[0]) * t)
        g = int(TOP[1] + (BOTTOM[1] - TOP[1]) * t)
        b = int(TOP[2] + (BOTTOM[2] - TOP[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b)
    return grad


def _draw_note(img):
    """Draw a clean white eighth-note centered on the tile."""
    s = img.size[0]
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)

    # Geometry scaled to tile size.
    head_w = int(s * 0.26)
    head_h = int(s * 0.20)
    head_x = int(s * 0.30)
    head_y = int(s * 0.60)
    # note head (tilted ellipse approximated by a filled ellipse)
    d.ellipse([head_x, head_y, head_x + head_w, head_y + head_h], fill=white)

    # stem
    stem_w = max(3, int(s * 0.045))
    stem_x = head_x + head_w - stem_w
    stem_top = int(s * 0.26)
    stem_bottom = head_y + int(head_h * 0.5)
    d.rounded_rectangle([stem_x, stem_top, stem_x + stem_w, stem_bottom],
                        radius=stem_w // 2, fill=white)

    # flag
    flag = [
        (stem_x + stem_w, stem_top),
        (stem_x + stem_w + int(s * 0.20), stem_top + int(s * 0.12)),
        (stem_x + stem_w + int(s * 0.15), stem_top + int(s * 0.30)),
        (stem_x + stem_w, stem_top + int(s * 0.17)),
    ]
    d.polygon(flag, fill=white)


def make(size):
    base = _gradient(size).convert("RGBA")
    radius = int(size * 0.22)
    mask = _rounded_mask(size, radius)
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tile.paste(base, (0, 0), mask)
    _draw_note(tile)
    return tile


def main():
    big = make(512)
    web_dir = os.path.join(ROOT, "mediahotkey", "web")
    os.makedirs(web_dir, exist_ok=True)
    big.save(os.path.join(web_dir, "logo.png"))

    ico_path = os.path.join(HERE, "icon.ico")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big.save(ico_path, sizes=[(s, s) for s in sizes])
    print("wrote", ico_path)
    print("wrote", os.path.join(web_dir, "logo.png"))


if __name__ == "__main__":
    main()
