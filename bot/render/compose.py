"""Паспорт = готовый бланк тотема + фото гостя + его имя и номер.

Бланки лежат в assets/templates/ и пекутся заранее (tools/bake_templates.py).
Всё, что зависит только от тотема — девять... восемь значений полей, зверь,
круглая печать, следы, фактура биометрии, пятно на карте, координаты, девиз —
на них уже напечатано. Считать это на каждого гостя незачем.

От гостя к гостю меняются ровно три вещи, и все три здесь.
Координаты — в системе бланка 1920×1086, замерены по пикселям исходника.
"""

from __future__ import annotations

import io
import pathlib
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "assets" / "templates"

INK = (58, 48, 36)

# Шрифт лежит в репозитории, а не в системе: бот деплоится на Linux, где
# системного Courier нет — с ним рендер падал на КАЖДОМ госте, а ошибку
# проглатывал общий except в хендлере, и гость видел «что-то сломалось».
VALUE_FONT = str(ROOT / "assets" / "fonts" / "JetBrainsMono.ttf")

# Ширина знака у обоих шрифтов 0.6 em, а капитель у JetBrains выше: 0.73
# против курьеровских 0.571. Пересчитываем кегль здесь — тогда все размеры
# и координаты, замеренные по макету, остаются в силе.
CAP_SCALE = 0.571 / 0.73

PHOTO_BOX = (117, 195, 609, 791)
NAME_ROW = (266, 306)          # верх и низ строки NAME; текст от x=700
NAME_RIGHT = 1130


@dataclass(frozen=True)
class Passport:
    name: str
    serial: str
    code: str
    totem_id: str


@lru_cache(maxsize=256)
def _font(size: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(VALUE_FONT, max(round(size * CAP_SCALE), 6))
    font.set_variation_by_name("Bold")     # макет набран жирной машинописью
    return font


def _tracked(draw, xy, text, font, fill, tracking=0.0, anchor="la") -> None:
    widths = [draw.textlength(c, font=font) for c in text]
    total = sum(widths) + tracking * max(len(text) - 1, 0)
    x, y = xy
    if anchor[0] == "m":
        x -= total / 2
    elif anchor[0] == "r":
        x -= total
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill, anchor="l" + anchor[1])
        x += w + tracking


def _fitted(draw, text, max_width, start, tracking, floor=0.55):
    """Подбирает кегль под ширину: длину имени гость выбирает сам."""
    size = start
    while size > start * floor:
        font = _font(round(size))
        width = sum(draw.textlength(c, font=font) for c in text) + tracking * max(len(text) - 1, 0)
        if width <= max_width:
            return font
        size *= 0.96
    return _font(round(start * floor))


def _place_photo(canvas: Image.Image, src: Image.Image) -> None:
    left, top, right, bottom = PHOTO_BOX
    src = src.convert("RGB")
    ratio = (right - left) / (bottom - top)
    if src.width / src.height > ratio:
        w = round(src.height * ratio)
        src = src.crop(((src.width - w) // 2, 0, (src.width + w) // 2, src.height))
    else:
        h = round(src.width / ratio)
        src = src.crop((0, 0, src.width, h))   # сверху: голова важнее подбородка
    canvas.paste(src.resize((right - left, bottom - top), Image.LANCZOS), (left, top))


def render(data: Passport, photo: "pathlib.Path | Image.Image | None") -> Image.Image:
    canvas = Image.open(TEMPLATES / f"{data.totem_id}.png").convert("RGB")

    if isinstance(photo, Image.Image):
        _place_photo(canvas, photo)
    elif photo is not None and photo.exists():
        _place_photo(canvas, Image.open(photo))

    d = ImageDraw.Draw(canvas)
    top, bottom = NAME_ROW
    font = _fitted(d, data.name, NAME_RIGHT - 700, (bottom - top) * 1.62, 0.5)
    _tracked(d, (700, (top + bottom) / 2), data.name, font, INK, tracking=0.5, anchor="lm")

    _tracked(d, (1650, 81), data.serial, _font(30), INK, tracking=1.0, anchor="lm")
    _tracked(d, (140, 167), f"SPP - KZ - {data.code}", _font(21), INK, tracking=1.4, anchor="lm")
    return canvas


def render_from_bytes(data: Passport, portrait: bytes) -> Image.Image:
    """Вариант для бота: фото приходит в памяти и на диск не ложится."""
    return render(data, Image.open(io.BytesIO(portrait)))
