"""Unguarded v2 graphics: white fill, #0A0A0A stroke, one #FF2D2D word, soft shadow. No gold."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import EMOJI_DIR, FONTS

WHITE = (255, 255, 255, 255)
RED = (255, 45, 45, 255)
INK = (10, 10, 10, 255)
SS = 2

_EMOJI_FONT = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"


def _font_path(prefer: str) -> str:
    candidate = FONTS / prefer
    if candidate.exists() and candidate.stat().st_size > 1000:
        return str(candidate)
    fallbacks = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ]
    for path in fallbacks:
        if Path(path).exists():
            return path
    raise RuntimeError("Ingen fet sans-font hittades. Kör startskriptet så laddas Anton och Montserrat ner.")


@lru_cache(maxsize=32)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _soft_shadow(image: Image.Image, dx: int, dy: int, blur: float, opacity: float) -> Image.Image:
    pad = int(blur * 3 + max(abs(dx), abs(dy)) + 2)
    w, h = image.size
    base = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    alpha = Image.new("L", base.size, 0)
    alpha.paste(image.split()[3], (pad + dx, pad + dy))
    alpha = alpha.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * opacity))
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow.putalpha(alpha)
    base.alpha_composite(shadow)
    base.alpha_composite(image, (pad, pad))
    bbox = base.getbbox()
    return base.crop(bbox) if bbox else base


def _draw_line(text: str, size: int, fill, stroke: int, font_file: str, max_width: int) -> Image.Image:
    while size >= 36:
        font = _font(font_file, int(size * SS))
        if font.getlength(text) / SS <= max_width:
            break
        size = int(size * 0.92)
    font = _font(font_file, int(size * SS))
    bbox = font.getbbox(text or "Hg")
    width = int(font.getlength(text) + (stroke * 2 + 8) * SS)
    height = int((bbox[3] - bbox[1]) + (stroke * 2 + 8) * SS)
    image = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.text(
        ((stroke + 4) * SS, (stroke + 4) * SS - bbox[1]),
        text,
        font=font,
        fill=fill,
        stroke_width=int(stroke * SS),
        stroke_fill=INK,
    )
    image = image.resize((max(1, image.width // SS), max(1, image.height // SS)), Image.Resampling.LANCZOS)
    image = _soft_shadow(image, 0, 6, 14, 0.45)
    return _soft_shadow(image, 0, 2, 3, 0.30)


def word_sprite(text: str, size: int, fill, stroke: int, font_file: str) -> Image.Image:
    return _draw_line(text, size, fill, stroke, font_file, 940)


def caption_sprites(text: str, accent: str, size: int = 72) -> list[Image.Image]:
    font_file = _font_path("Montserrat-ExtraBold.ttf")
    words = text.split() or [text]
    accent_l = (accent or "").strip().lower()
    sprites = []
    highlighted = False
    for word in words:
        use_red = word.lower().strip(".,!?") == accent_l and not highlighted
        if use_red:
            highlighted = True
        sprites.append(word_sprite(word, size, RED if use_red else WHITE, max(6, size // 12), font_file))
    if not highlighted and sprites:
        # One red word, as the style spec requires.
        index = max(range(len(words)), key=lambda i: len(words[i]))
        sprites[index] = word_sprite(words[index], size, RED, max(6, size // 12), font_file)
    return sprites


def headline_sprite(text: str, accent: str | None, size: int = 120) -> Image.Image:
    font_file = _font_path("Anton-Regular.ttf")
    words = text.upper().split() or [text.upper()]
    accent_u = (accent or words[-1]).upper()
    sprites = []
    highlighted = False
    for word in words:
        use_red = word == accent_u and not highlighted
        if use_red:
            highlighted = True
        sprites.append(word_sprite(word, size, RED if use_red else WHITE, max(8, int(size * 0.075)), font_file))
    if not highlighted:
        sprites[-1] = word_sprite(words[-1], size, RED, max(8, int(size * 0.075)), font_file)
    return _row(sprites, gap=int(size * 0.18))


def story_sprite(text: str, size: int = 64) -> Image.Image:
    return _draw_line(text, size, WHITE, 6, _font_path("Montserrat-ExtraBold.ttf"), 960)


def bubble_sprite(text: str, size: int = 48) -> Image.Image:
    font = _font(_font_path("Montserrat-ExtraBold.ttf"), int(size * SS))
    words = text.split() or [text]
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = (cur + " " + word).strip()
        if font.getlength(trial) / SS > 600 and cur:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    line_h = int(size * 1.18 * SS)
    text_w = max(font.getlength(line) for line in lines)
    pad_x, pad_y = int(size * 0.7 * SS), int(size * 0.45 * SS)
    bw, bh = int(text_w + 2 * pad_x), int(line_h * len(lines) + 2 * pad_y)
    margin = int(70 * SS)
    image = Image.new("RGBA", (bw + 2 * margin, bh + 2 * margin), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([margin, margin, margin + bw, margin + bh], radius=min(bh // 2, int(48 * SS)), fill=WHITE)
    for i, line in enumerate(lines):
        tw = font.getlength(line)
        draw.text((margin + (bw - tw) / 2, margin + pad_y + i * line_h), line, font=font, fill=(28, 28, 32, 255))
    image = image.resize((image.width // SS, image.height // SS), Image.Resampling.LANCZOS)
    bbox = image.getbbox()
    image = image.crop(bbox) if bbox else image
    return _soft_shadow(image, 0, 8, 16, 0.35)


def badge_sprite(text: str, size: int = 54) -> Image.Image:
    font = _font(_font_path("Anton-Regular.ttf"), int(size * SS))
    label = text.upper()
    tw = font.getlength(label)
    pad_x, pad_y = int(22 * SS), int(10 * SS)
    w, h = int(tw + pad_x * 2), int(size * SS + pad_y * 2)
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(14 * SS), fill=RED)
    bbox = font.getbbox(label)
    draw.text(((w - tw) / 2, (h - (bbox[3] - bbox[1])) / 2 - bbox[1]), label, font=font, fill=WHITE)
    image = image.resize((w // SS, h // SS), Image.Resampling.LANCZOS)
    return _soft_shadow(image, 0, 6, 12, 0.4)


def arrow_sprite(length: int = 210) -> Image.Image:
    shaft, head, head_l = 24, 70, 78
    pad = 40
    W, H = (length + 2 * pad) * SS, (head + 2 * pad) * SS
    cy = H / 2
    x0, x1 = pad * SS, (pad + length) * SS
    hx = x1 - head_l * SS
    pts = [
        (x0, cy - shaft * SS / 2), (hx, cy - shaft * SS / 2), (hx, cy - head * SS / 2),
        (x1, cy), (hx, cy + head * SS / 2), (hx, cy + shaft * SS / 2), (x0, cy + shaft * SS / 2),
    ]
    image = Image.new("RGBA", (int(W), int(H)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.polygon(pts, fill=WHITE)
    inner = [(pts[0][0] + 8 * SS, pts[0][1] + 4 * SS), (hx - 4 * SS, cy - shaft * SS / 2 + 6 * SS),
             (hx - 4 * SS, cy - head * SS / 2 + 8 * SS), (x1 - 10 * SS, cy),
             (hx - 4 * SS, cy + head * SS / 2 - 8 * SS), (hx - 4 * SS, cy + shaft * SS / 2 - 6 * SS),
             (pts[0][0] + 8 * SS, pts[0][1] + shaft * SS - 4 * SS)]
    draw.polygon(inner, fill=RED)
    image = image.resize((int(W) // SS, int(H) // SS), Image.Resampling.LANCZOS)
    bbox = image.getbbox()
    image = image.crop(bbox) if bbox else image
    return _soft_shadow(image, 0, 6, 10, 0.4)


def circle_sprite(radius: int = 90) -> Image.Image:
    thick = 10
    pad = 24
    size = int((radius + thick + pad) * 2 * SS)
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    c = size / 2
    outer = (radius + thick) * SS
    draw.ellipse([c - outer, c - outer, c + outer, c + outer], outline=RED, width=int(thick * SS))
    inner = (radius - 4) * SS
    draw.ellipse([c - inner, c - inner, c + inner, c + inner], outline=WHITE, width=int(3 * SS))
    image = image.resize((size // SS, size // SS), Image.Resampling.LANCZOS)
    return _soft_shadow(image, 0, 5, 8, 0.4)


def emoji_sprite(emoji: str, size: int = 150) -> Image.Image:
    path = EMOJI_DIR / f"{_slug(emoji)}.png"
    if path.exists():
        image = Image.open(path).convert("RGBA")
        image = image.crop(image.getbbox() or (0, 0, image.width, image.height))
        scale = size / max(image.size)
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
        return _soft_shadow(image, 0, 8, 10, 0.35)
    return _emoji_font_sprite(emoji, size)


def _emoji_font_sprite(emoji: str, size: int) -> Image.Image:
    if Path(_EMOJI_FONT).exists():
        try:
            font = ImageFont.truetype(_EMOJI_FONT, 109)
            dummy = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
            draw = ImageDraw.Draw(dummy)
            bbox = draw.textbbox((0, 0), emoji, font=font)
            w, h = bbox[2] - bbox[0] + 8, bbox[3] - bbox[1] + 8
            image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(image).text((4 - bbox[0], 4 - bbox[1]), emoji, font=font, embedded_color=True)
            scale = size / max(image.size)
            image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
            return _soft_shadow(image, 0, 8, 10, 0.35)
        except Exception:
            pass
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse([4, 4, size - 4, size - 4], fill=RED)
    return image


def _slug(emoji: str) -> str:
    return "-".join(f"{ord(ch):x}" for ch in emoji if ord(ch) > 32)


def _row(sprites: list[Image.Image], gap: int) -> Image.Image:
    height = max(s.height for s in sprites)
    width = sum(s.width for s in sprites) + gap * (len(sprites) - 1)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x = 0
    for sprite in sprites:
        image.alpha_composite(sprite, (x, (height - sprite.height) // 2))
        x += sprite.width + gap
    return image


def slam_scale(t: float) -> float:
    if t < 0.10:
        u = t / 0.10
        return 1.30 - 0.32 * (1 - (1 - u) ** 3)
    if t < 0.17:
        return 0.98 + 0.02 * ((t - 0.10) / 0.07)
    return 1.0


def pop_scale(t: float) -> float:
    if t >= 0.18:
        return 1.0
    u = t / 0.18 - 1
    k = 1.70158
    eased = 1 + (k + 1) * u**3 + k * u**2
    return 0.6 + 0.4 * eased


def blit(dst: np.ndarray, sprite: Image.Image, x: int, y: int) -> None:
    if sprite.width < 1 or sprite.height < 1:
        return
    arr = np.asarray(sprite)
    h, w = arr.shape[:2]
    dh, dw = dst.shape[:2]
    x0, y0 = int(x), int(y)
    sx0 = 0 if x0 >= 0 else -x0
    sy0 = 0 if y0 >= 0 else -y0
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(dw, x0 + w - sx0), min(dh, y0 + h - sy0)
    if x1 <= x0 or y1 <= y0:
        return
    crop = arr[sy0 : sy0 + (y1 - y0), sx0 : sx0 + (x1 - x0)]
    alpha = crop[:, :, 3:4].astype(np.float32) / 255.0
    if alpha.max() <= 0:
        return
    rgb = crop[:, :, :3].astype(np.float32)
    region = dst[y0:y1, x0:x1].astype(np.float32)
    dst[y0:y1, x0:x1] = np.clip(rgb * alpha + region * (1 - alpha), 0, 255).astype(np.uint8)


def scale_sprite(sprite: Image.Image, scale: float) -> Image.Image:
    scale = max(0.2, min(scale, 1.6))
    if abs(scale - 1) < 0.02:
        return sprite
    w = max(1, int(sprite.width * scale))
    h = max(1, int(sprite.height * scale))
    return sprite.resize((w, h), Image.Resampling.LANCZOS)


def contains_gold(sprite: Image.Image) -> bool:
    arr = np.asarray(sprite.convert("RGBA"))
    rgb = arr[:, :, :3].astype(np.int16)
    alpha = arr[:, :, 3]
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    # Gold-ish: high red and green, low blue, not a neutral white.
    gold = (alpha > 40) & (r > 180) & (g > 140) & (b < 80) & (r - b > 100) & (g - b > 60)
    return bool(gold.any())
