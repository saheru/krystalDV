"""Generate placeholder application icons (kdv.ico + kdv.icns).

Run once on a developer machine:
    python build/make_icon.py

Produces:
    assets/icons/kdv.ico   (Windows multi-size)
    assets/icons/kdv.icns  (macOS, only generated on macOS — needs `iconutil`)

Replace either result with your own designed icons whenever you like.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ICONS_DIR = Path(__file__).resolve().parents[1] / "assets" / "icons"
ICONS_DIR.mkdir(parents=True, exist_ok=True)

PRIMARY = (91, 108, 255, 255)   # #5B6CFF
ACCENT = (139, 151, 255, 255)
WHITE_SOFT = (255, 255, 255, 230)


def make_size(px: int) -> Image.Image:
    img = Image.new("RGBA", (px, px), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    radius = px // 5
    d.rounded_rectangle((0, 0, px - 1, px - 1), radius=radius, fill=PRIMARY)
    cx = cy = px // 2
    s = px // 4
    d.polygon(
        [(cx, cy - s), (cx + s, cy), (cx, cy + s), (cx - s, cy)],
        fill=WHITE_SOFT,
    )
    d.polygon(
        [(cx, cy - s // 2), (cx + s // 2, cy), (cx, cy + s // 2), (cx - s // 2, cy)],
        fill=ACCENT,
    )
    return img


def write_ico() -> Path:
    """Windows multi-size .ico."""
    sizes = [16, 24, 32, 48, 64, 128, 256]
    out = ICONS_DIR / "kdv.ico"
    base = make_size(256)
    base.save(out, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"wrote {out}")
    return out


def write_icns() -> Path | None:
    """macOS .icns — requires `iconutil` (preinstalled on macOS)."""
    if sys.platform != "darwin":
        print("skipping .icns (only generated on macOS)")
        return None
    if not shutil.which("iconutil"):
        print("iconutil not found; skipping .icns")
        return None

    # Apple-recommended sizes inside an .iconset bundle.
    sizes = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]
    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / "kdv.iconset"
        iconset.mkdir()
        for px, name in sizes:
            make_size(px).save(iconset / name, format="PNG")
        out = ICONS_DIR / "kdv.icns"
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
            check=True,
        )
    print(f"wrote {out}")
    return out


def main() -> None:
    write_ico()
    write_icns()


if __name__ == "__main__":
    main()
