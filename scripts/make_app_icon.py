"""Genera praxeon/desktop/assets/praxeon.ico dibujando el isotipo (praxeon-isotipo.svg) sin dependencias de SVG.

Uso:  uv run --with pillow python scripts/make_app_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

BG, FG = (14, 16, 19, 255), (228, 230, 233, 255)       # --bg y --text de la interfaz
SIZES = [16, 24, 32, 48, 64, 128, 256]
OUT = Path(__file__).resolve().parent.parent / "praxeon" / "desktop" / "assets" / "praxeon.ico"


def render(size: int) -> Image.Image:
    s = 8                                                # supersampling para bordes suaves
    big = size * s
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * 0.22), fill=BG)

    # Isotipo en su caja original 52 x 62.04, ocupando el 54 % del alto del icono y centrado
    scale = big * 0.54 / 62.04
    ox = (big - 52 * scale) / 2
    oy = (big - 62.04 * scale) / 2
    X = lambda x: ox + x * scale
    Y = lambda y: oy + y * scale
    d.rectangle([X(0), Y(0), X(14.60), Y(62.04)], fill=FG)
    # Media luna: rectángulo 21.89..32.84 y semicírculo de radio 19.16 centrado en (32.84, 19.16)
    d.rectangle([X(21.89), Y(0), X(32.84), Y(38.32)], fill=FG)
    r = 19.16
    d.pieslice([X(32.84 - r), Y(19.16 - r), X(32.84 + r), Y(19.16 + r)], -90, 90, fill=FG)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames = [render(n) for n in SIZES]
    frames[-1].save(OUT, format="ICO", sizes=[(n, n) for n in SIZES], append_images=frames[:-1])
    print("escrito", OUT, OUT.stat().st_size, "bytes")
