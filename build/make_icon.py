"""Generate a placeholder application icon (kdv.ico) so the build doesn't fail.

Run once on a developer machine that has Pillow installed:
    python build/make_icon.py

You can replace the result with your own designed .ico file at any time.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "assets" / "icons" / "kdv.ico"
OUT.parent.mkdir(parents=True, exist_ok=True)

PRIMARY = (91, 108, 255, 255)  # #5B6CFF
ACCENT = (139, 151, 255, 255)


def make_size(px: int) -> Image.Image:
    img = Image.new("RGBA", (px, px), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    # rounded square (manual: filled circle masked corners)
    radius = px // 5
    d.rounded_rectangle((0, 0, px - 1, px - 1), radius=radius, fill=PRIMARY)
    # diamond accent in the middle
    cx = cy = px // 2
    s = px // 4
    d.polygon(
        [(cx, cy - s), (cx + s, cy), (cx, cy + s), (cx - s, cy)],
        fill=(255, 255, 255, 230),
    )
    d.polygon(
        [(cx, cy - s // 2), (cx + s // 2, cy), (cx, cy + s // 2), (cx - s // 2, cy)],
        fill=ACCENT,
    )
    return img


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base = make_size(256)
    base.save(
        OUT,
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
