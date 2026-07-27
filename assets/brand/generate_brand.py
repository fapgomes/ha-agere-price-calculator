#!/usr/bin/env python3
"""Generate the brand images for the AGERE Water Price integration.

Renders a neutral water-drop + euro mark (no third-party trademarks) into the
PNG set expected by Home Assistant, both for the local ``brand/`` folder and for
a ``home-assistant/brands`` pull request.

Sizes follow https://github.com/home-assistant/brands#requirements:
  icon  -> 256x256 (1:1) and 512x512 for @2x
  logo  -> landscape, shortest side 128-256 (normal) and 256-512 (@2x)
Images are trimmed of empty edges, transparent, optimised and interlaced.

Usage: python3 assets/brand/generate_brand.py <output_dir> [output_dir ...]
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_BOLD = "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf"

DROP_TOP = (0x35, 0xC0, 0xF5)
DROP_BOTTOM = (0x01, 0x54, 0x9B)
GLYPH = (0xFF, 0xFF, 0xFF, 0xFF)

TEXT_LIGHT = ((0x1B, 0x2A, 0x33, 0xFF), (0x54, 0x6E, 0x7A, 0xFF))
TEXT_DARK = ((0xFF, 0xFF, 0xFF, 0xFF), (0xB0, 0xBE, 0xC5, 0xFF))

# Everything is drawn at this height and downscaled, for clean antialiasing.
RENDER_H = 1280
DROP_RATIO = 2.9  # drop height / body radius


def drop_polygon(cx: float, cy: float, r: float, h: float, steps: int = 480):
    """Outline of a teardrop: a circle closed by the two tangents from an apex.

    Using the tangent points keeps the transition from the straight sides into
    the round body smooth, which is what makes the shape read as a droplet.
    """
    alpha = math.acos(r / h)
    points = [(cx, cy - h)]
    for i in range(steps + 1):
        theta = alpha + (2 * math.pi - 2 * alpha) * i / steps
        points.append((cx + r * math.sin(theta), cy - r * math.cos(theta)))
    return points


def draw_drop(canvas: Image.Image, left: float, top: float, height: float) -> float:
    """Draw the gradient droplet with a euro glyph; returns its width."""
    r = height / DROP_RATIO
    h = height - r
    cx = left + r
    cy = top + h
    width = 2 * r

    gradient = Image.new("RGB", (1, round(height)), DROP_BOTTOM)
    px = gradient.load()
    for y in range(gradient.height):
        t = y / max(gradient.height - 1, 1)
        px[0, y] = tuple(round(a + (b - a) * t) for a, b in zip(DROP_TOP, DROP_BOTTOM))
    gradient = gradient.resize((round(width), round(height)), Image.Resampling.BILINEAR)

    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).polygon(drop_polygon(cx, cy, r, h), fill=255)
    tinted = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    tinted.paste(gradient, (round(left), round(top)))
    canvas.paste(tinted, (0, 0), mask)

    # The euro sign sits in the round body, not the visual centre of the drop.
    font = ImageFont.truetype(FONT_BOLD, round(1.55 * r))
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), "€", font=font)
    draw.text(
        (cx - (bbox[0] + bbox[2]) / 2, cy - (bbox[1] + bbox[3]) / 2),
        "€",
        font=font,
        fill=GLYPH,
    )
    return width


def trim(image: Image.Image) -> Image.Image:
    bbox = image.getbbox()
    return image.crop(bbox) if bbox else image


def render_icon(size: int) -> Image.Image:
    canvas = Image.new("RGBA", (RENDER_H, RENDER_H), (0, 0, 0, 0))
    height = RENDER_H * 0.98
    width = height / DROP_RATIO * 2
    draw_drop(canvas, (RENDER_H - width) / 2, (RENDER_H - height) / 2, height)

    # Icons must be 1:1, so the trimmed drop is padded only to square it off.
    drop = trim(canvas)
    side = max(drop.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(drop, ((side - drop.width) // 2, (side - drop.height) // 2))
    return square.resize((size, size), Image.Resampling.LANCZOS)


def render_logo(height: int, dark: bool) -> Image.Image:
    h = RENDER_H
    canvas = Image.new("RGBA", (h * 4, h), (0, 0, 0, 0))
    drop_w = draw_drop(canvas, 0, 0, h)

    primary, secondary = TEXT_DARK if dark else TEXT_LIGHT
    top_font = ImageFont.truetype(FONT_BOLD, round(0.30 * h))
    bottom_font = ImageFont.truetype(FONT_REGULAR, round(0.21 * h))
    draw = ImageDraw.Draw(canvas)

    x = drop_w + 0.10 * h
    top_bbox = draw.textbbox((0, 0), "AGERE", font=top_font)
    bottom_bbox = draw.textbbox((0, 0), "Water Price", font=bottom_font)
    gap = 0.06 * h
    block = (top_bbox[3] - top_bbox[1]) + gap + (bottom_bbox[3] - bottom_bbox[1])
    y = (h - block) / 2

    draw.text((x - top_bbox[0], y - top_bbox[1]), "AGERE", font=top_font, fill=primary)
    y += (top_bbox[3] - top_bbox[1]) + gap
    draw.text(
        (x - bottom_bbox[0], y - bottom_bbox[1]),
        "Water Price",
        font=bottom_font,
        fill=secondary,
    )

    logo = trim(canvas)
    width = round(logo.width * height / logo.height)
    return logo.resize((width, height), Image.Resampling.LANCZOS)


def save(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)
    # The brands guidelines ask for optimised, interlaced PNGs.
    subprocess.run(
        [
            "magick",
            str(path),
            "-strip",
            "-interlace",
            "PNG",
            "-define",
            "png:compression-level=9",
            "-define",
            "png:compression-filter=5",
            str(path),
        ],
        check=True,
    )


def main(targets: list[Path]) -> None:
    images = {
        "icon.png": render_icon(256),
        "icon@2x.png": render_icon(512),
        "logo.png": render_logo(160, dark=False),
        "logo@2x.png": render_logo(320, dark=False),
        "dark_logo.png": render_logo(160, dark=True),
        "dark_logo@2x.png": render_logo(320, dark=True),
    }
    for target in targets:
        for name, image in images.items():
            save(image, target / name)
            print(f"{target / name}: {image.width}x{image.height}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    main([Path(a) for a in args])
