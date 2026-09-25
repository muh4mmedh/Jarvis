"""Render the animated J.A.R.V.I.S. banner.

Draws the same arc reactor the HUD draws, frame by frame, into a seamlessly
looping GIF. Every rotation and pulse completes a whole number of cycles across
the frame count, so the last frame flows into the first with no visible seam.

    python docs/make_banner.py

Writes docs/hero.gif. Requires Pillow.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

DOCS = Path(__file__).resolve().parent

W, H = 1400, 600
FRAMES = 60
FPS = 20

BG = (3, 7, 13)
CYAN = (56, 224, 255)
DIM = (26, 127, 150)

CX, CY = W // 2, 250          # reactor centre
SS = 2                        # supersample factor for smooth curves


def font(size: int, bold: bool = False):
    """A monospace face if Windows has one, else whatever Pillow can find."""
    for name in (("consolab.ttf", "consola.ttf") if bold else ("consola.ttf",)) + \
                ("DejaVuSansMono.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def blend(colour, alpha: float):
    """Pillow has no per-shape alpha on RGB, so pre-blend against the background."""
    a = max(0.0, min(alpha, 1.0))
    return tuple(int(BG[i] + (colour[i] - BG[i]) * a) for i in range(3))


def ring(draw, r, segments, gap_deg, rot_deg, alpha, width):
    """One segmented ring, drawn as arcs with gaps between them."""
    box = [(CX - r) * SS, (CY - r) * SS, (CX + r) * SS, (CY + r) * SS]
    step = 360 / segments
    colour = blend(CYAN, alpha)
    for i in range(segments):
        a0 = rot_deg + i * step
        draw.arc(box, a0, a0 + step - gap_deg, fill=colour, width=max(1, int(width * SS)))


def background() -> Image.Image:
    """Grid, vignette and the ambient glow behind the reactor — all static,
    so it is built once and reused for every frame."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    for x in range(0, W, 56):
        d.line([(x, 0), (x, H)], fill=blend(CYAN, 0.05))
    for y in range(0, H, 56):
        d.line([(0, y), (W, y)], fill=blend(CYAN, 0.05))

    glow = Image.new("RGB", (W, H), BG)
    gd = ImageDraw.Draw(glow)
    for r, a in ((420, 0.06), (300, 0.09), (190, 0.14), (110, 0.18)):
        gd.ellipse([CX - r, CY - r * 0.9, CX + r, CY + r * 0.9], fill=blend(CYAN, a))
    glow = glow.filter(ImageFilter.GaussianBlur(70))
    return Image.blend(img, glow, 0.55)


def side_panel(draw, x, y, w, h, phase):
    """A floating glass readout panel with animated bars."""
    draw.rectangle([x, y, x + w, y + h], outline=blend(CYAN, 0.22))
    for dx, dy, ex, ey in ((0, 0, 13, 0), (0, 0, 0, 13),
                           (w, 0, -13, 0), (w, 0, 0, 13),
                           (0, h, 13, 0), (0, h, 0, -13),
                           (w, h, -13, 0), (w, h, 0, -13)):
        draw.line([(x + dx, y + dy), (x + dx + ex, y + dy + ey)], fill=blend(CYAN, 0.75), width=2)

    # waveform bars — the only moving part
    bars, bw = 22, (w - 30) / 22
    for i in range(bars):
        v = (math.sin(phase * 2 * math.pi + i * 0.55) + 1) / 2
        bh = 6 + v * (h * 0.34)
        bx = x + 15 + i * bw
        by = y + h * 0.72
        draw.rectangle([bx, by - bh, bx + bw * 0.55, by], fill=blend(CYAN, 0.30 + v * 0.55))

    # inert text-like rules
    for i in range(5):
        ly = y + 18 + i * 11
        draw.line([(x + 15, ly), (x + 15 + (w - 40) * (0.35 + 0.5 * ((i * 7) % 5) / 5), ly)],
                  fill=blend(CYAN, 0.16))


def frame(i: int, bg: Image.Image) -> Image.Image:
    t = i / FRAMES                      # 0..1 across the loop

    big = bg.resize((W * SS, H * SS), Image.NEAREST)
    d = ImageDraw.Draw(big)

    # Whole-number turn counts keep the loop seamless.
    ring(d, 186, 60, 2.2, -t * 360 * 1, 0.28, 1.5)
    ring(d, 168, 90, 1.2, t * 360 * 1, 0.16, 1)
    ring(d, 148, 6, 15.0, t * 360 * 1, 0.85, 7)
    ring(d, 126, 12, 9.0, -t * 360 * 2, 0.60, 4)
    ring(d, 104, 3, 30.0, t * 360 * 1, 1.00, 10)
    ring(d,  82, 24, 5.0, -t * 360 * 3, 0.50, 2.5)
    ring(d,  64, 2, 26.0, t * 360 * 2, 0.90, 6)

    # Core pulse: two full cycles per loop.
    pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi * 2)

    # halo, then the triangle, then a white-hot centre
    for r, a in ((52, 0.16), (44, 0.24), (36, 0.34)):
        rr = (r + pulse * 4) * SS
        d.ellipse([CX * SS - rr, CY * SS - rr, CX * SS + rr, CY * SS + rr],
                  fill=blend(CYAN, a * (0.7 + pulse * 0.3)))

    tri_r = (42 + pulse * 4) * SS
    ang = t * 2 * math.pi - math.pi / 2        # one full turn per loop
    pts = [(CX * SS + math.cos(ang + k * 2 * math.pi / 3) * tri_r,
            CY * SS + math.sin(ang + k * 2 * math.pi / 3) * tri_r) for k in range(3)]
    d.polygon(pts, outline=blend(CYAN, 0.75 + pulse * 0.25), width=int(4 * SS))

    for r, a in ((26, 0.55), (18, 0.85), (11, 1.0)):
        rr = (r + pulse * 3) * SS
        d.ellipse([CX * SS - rr, CY * SS - rr, CX * SS + rr, CY * SS + rr],
                  fill=blend(CYAN, a * (0.75 + pulse * 0.25)))

    hot = (7 + pulse * 2) * SS
    d.ellipse([CX * SS - hot, CY * SS - hot, CX * SS + hot, CY * SS + hot],
              fill=(235, 253, 255))

    img = big.resize((W, H), Image.LANCZOS)

    # Bloom pass — the glow that makes it read as holographic.
    bloom = img.filter(ImageFilter.GaussianBlur(15))
    img = Image.blend(img, bloom, 0.46)
    wide = img.filter(ImageFilter.GaussianBlur(45))
    img = Image.blend(img, wide, 0.20)

    d = ImageDraw.Draw(img)
    side_panel(d, 120, 150, 250, 190, t)
    side_panel(d, W - 370, 150, 250, 190, t + 0.5)

    title = font(60, bold=True)
    sub = font(17)
    d.text((CX, 448), "J . A . R . V . I . S .", font=title,
           fill=blend(CYAN, 0.95), anchor="mm")
    d.text((CX, 508), "JUST  A  RATHER  VERY  INTELLIGENT  SYSTEM", font=sub,
           fill=DIM, anchor="mm")

    return img


def main() -> None:
    bg = background()
    frames = [frame(i, bg) for i in range(FRAMES)]
    print(f"  rendered {len(frames)} frames")

    # Quantise every frame against one shared palette so colours do not shift
    # between frames (which looks like flickering in a GIF).
    master = frames[0].quantize(colors=200, method=Image.MEDIANCUT)
    quantised = [f.quantize(palette=master, dither=Image.FLOYDSTEINBERG) for f in frames]

    out = DOCS / "hero.gif"
    quantised[0].save(
        out, save_all=True, append_images=quantised[1:],
        duration=int(1000 / FPS), loop=0, optimize=True, disposal=2,
    )
    print(f"  {out.name}  {W}x{H}  {FRAMES} frames  {out.stat().st_size / 1e6:.1f} MB")

    still = DOCS / "hero-still.png"
    frames[0].save(still)
    print(f"  {still.name}  {still.stat().st_size / 1e3:.0f} KB")


if __name__ == "__main__":
    main()
