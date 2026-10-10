"""Make the MSIX tile/logo images and the .exe icon from one square source image.

    python packaging\\make_assets.py --out build\\assets [--icon path\\to\\icon-1024.png]

Without --icon a plain placeholder mark is drawn. Replace it with the real PAi Health
logo before submitting (a 1024x1024 PNG with a transparent background works best).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NAVY, TEAL, WHITE = (11, 27, 51, 255), (20, 160, 150, 255), (255, 255, 255, 255)

# name -> (base width, base height); every one gets scale-100/200/400
TILES = {
    "Square44x44Logo": (44, 44),
    "Square150x150Logo": (150, 150),
    "Wide310x150Logo": (310, 150),
    "StoreLogo": (50, 50),
    "SmallTile": (71, 71),
    "LargeTile": (310, 310),
    "SplashScreen": (620, 300),
}
SCALES = (100, 200, 400)
TARGET_SIZES = (16, 24, 32, 48, 256)       # taskbar / Start list icons
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _font(px: int):
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default(size=px)


def placeholder(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size // 16
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=size // 5, fill=NAVY)
    f = _font(size // 3)
    text = "PAi"
    box = d.textbbox((0, 0), text, font=f)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], size * 0.40 - h / 2 - box[1]), text, font=f, fill=WHITE)
    y = int(size * 0.70)
    pts = [(size * 0.22, y), (size * 0.40, y), (size * 0.46, y - size * 0.09), (size * 0.53, y + size * 0.07),
           (size * 0.59, y), (size * 0.78, y)]
    d.line(pts, fill=TEAL, width=max(2, size // 40), joint="curve")
    return img


def fit(src: Image.Image, w: int, h: int, fill: float) -> Image.Image:
    """Centre the mark on a transparent canvas, at `fill` of the shorter side."""
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    side = max(1, int(min(w, h) * fill))
    mark = src.resize((side, side), Image.LANCZOS)
    canvas.paste(mark, ((w - side) // 2, (h - side) // 2), mark)
    return canvas


def build(out: Path, icon: Path | None) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    src = Image.open(icon).convert("RGBA") if icon else placeholder()
    if src.width != src.height:
        raise SystemExit("The source icon must be square")
    written = []
    for name, (bw, bh) in TILES.items():
        square = bw == bh
        fill = 1.0 if square and name != "LargeTile" else (0.66 if square else 0.8)
        for s in SCALES:
            w, h = bw * s // 100, bh * s // 100
            p = out / f"{name}.scale-{s}.png"
            fit(src, w, h, fill).save(p)
            written.append(p)
    for t in TARGET_SIZES:
        for suffix in ("", "_altform-unplated", "_altform-lightunplated"):
            p = out / f"Square44x44Logo.targetsize-{t}{suffix}.png"
            fit(src, t, t, 1.0).save(p)
            written.append(p)
    ico = out / "PAiHealth.ico"
    src.resize((256, 256), Image.LANCZOS).save(ico, sizes=ICO_SIZES)
    written.append(ico)
    store = out / "listing"
    store.mkdir(exist_ok=True)
    for px in (300, 2160):                     # Partner Center: 1:1 app tile icon 300x300 / box art 2160x2160
        p = store / f"StoreIcon-{px}.png"
        src.resize((px, px), Image.LANCZOS).save(p)
        written.append(p)
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--icon")
    a = ap.parse_args()
    files = build(Path(a.out), Path(a.icon) if a.icon else None)
    print(f"{len(files)} images written to {a.out}" + ("" if a.icon else "  (PLACEHOLDER mark — supply --icon)"))


if __name__ == "__main__":
    main()
