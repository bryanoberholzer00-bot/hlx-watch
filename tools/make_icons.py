"""Genera le icone di HLX Watch (sito e app Android) disegnando una lambda arancione su fondo scuro.

Uso: python tools/make_icons.py   (rigenera i PNG in hlxwatch/reports/site_static e android/app/src/main/res)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BG = (31, 36, 48, 255)          # #1f2430, come l'intestazione del sito
FG = (232, 116, 59, 255)        # #e8743b, l'arancio del sito


def draw_icon(size: int, round_shape: bool = False, padding: float = 0.0) -> Image.Image:
    scale = 4                                              # disegno grande e riduco: bordi lisci
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if round_shape:
        d.ellipse([0, 0, s - 1, s - 1], fill=BG)
    else:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=BG)
    inner = s * (1 - 2 * padding)
    off = s * padding

    def p(x: float, y: float) -> tuple[float, float]:
        return (off + x * inner, off + y * inner)

    # lambda come due poligoni pieni (coordinate nel quadrato unitario):
    # gamba lunga con grazia in alto e piede in basso, poi gamba corta verso il basso a sinistra
    d.polygon([p(0.28, 0.16), p(0.42, 0.16), p(0.76, 0.76), p(0.84, 0.76), p(0.84, 0.86),
               p(0.68, 0.86), p(0.346, 0.27), p(0.28, 0.27)], fill=FG)
    d.polygon([p(0.50, 0.42), p(0.575, 0.55), p(0.30, 0.88), p(0.19, 0.80)], fill=FG)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    static = ROOT / "hlxwatch" / "reports" / "site_static"
    static.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        draw_icon(size).save(static / f"icon-{size}.png")
    draw_icon(512, padding=0.12).save(static / "icon-maskable-512.png")   # con margine per le icone "maschera"
    draw_icon(180).save(static / "apple-touch-icon.png")
    draw_icon(64).save(static / "favicon.png")

    res = ROOT / "android" / "app" / "src" / "main" / "res"
    for density, size in (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)):
        folder = res / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        draw_icon(size).save(folder / "ic_launcher.png")
        draw_icon(size, round_shape=True).save(folder / "ic_launcher_round.png")
    print("icone generate")


if __name__ == "__main__":
    main()
