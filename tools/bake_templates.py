#!/usr/bin/env python3
"""Выпечка четырёх бланков — по одному на тотем.

От гостя к гостю меняются ровно три вещи: фото, имя и номер. Всё остальное
зависит ТОЛЬКО от тотема — значения девяти полей, зверь, круглая печать,
следы, фактура биометрии, пятно на карте, координаты, девиз. Считать это на
каждого гостя незачем: печём один раз в assets/templates/ и в боте только
кладём поверх фото и имя.

Пустой бланк (assets/source/template.png) — исходный макет, с которого снято
всё переменное; его готовит tools/clean_template.py. Подписи полей, заголовок,
цитаты, пословица, рамка, флаг, золотая печать и тамга остаются РОДНОЙ
графикой макета: их не набрать заново тем же шрифтом и той же фактурой.

Координаты — в системе бланка 1920×1086, замерены по пикселям исходника.

    python3 tools/bake_templates.py        # после правки content/totems.yaml
"""

from __future__ import annotations

import hashlib
import math
import pathlib
import zlib
from functools import lru_cache

import numpy as np
import yaml
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import signs

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "source" / "template.png"
GENERATED = ROOT / "assets" / "generated"
OUT = ROOT / "assets" / "templates"

INK = (58, 48, 36)
INK_SOFT = (108, 94, 74)
BLUE = (44, 74, 108)

# Шрифт лежит в репозитории, а не в системе: бот деплоится на Linux, где
# системного Courier нет — с ним рендер падал на КАЖДОМ госте, а ошибку
# проглатывал общий except в хендлере, и гость видел «что-то сломалось».
VALUE_FONT = str(ROOT / "assets" / "fonts" / "JetBrainsMono.ttf")

# Ширина знака у обоих шрифтов 0.6 em, а капитель у JetBrains выше: 0.73
# против курьеровских 0.571. Пересчитываем кегль здесь — тогда все размеры
# и координаты, замеренные по макету, остаются в силе.
CAP_SCALE = 0.571 / 0.73

# Замерено по исходнику: (верх, низ) каждой строки значений; текст от x=700.
# Первая строка — имя, её печатает бот, здесь она остаётся пустой.
VALUE_ROWS = [
    (337, 368), (400, 431), (460, 493), (521, 555),
    (581, 612), (640, 671), (699, 730), (758, 789),
]
FIELD_ORDER = ["origin", "national_symbol", "element", "territory",
               "language_of_land", "status", "issued_by", "validity"]

ANIMAL_BOX = (1050, 300, 1650, 740)
STAMP_CENTER, STAMP_R = (1716, 516), 120
MAP_BOX = (352, 812, 620, 962)
TRACK_BOX = (636, 868, 946, 950)
TEXTURE_BOX = (1000, 866, 1386, 950)


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
    """Подбирает кегль под ширину. Значения правятся в YAML — длина непредсказуема."""
    size = start
    while size > start * floor:
        font = _font(round(size))
        width = sum(draw.textlength(c, font=font) for c in text) + tracking * max(len(text) - 1, 0)
        if width <= max_width:
            return font
        size *= 0.96
    return _font(round(start * floor))


def _ink_layer(mask: Image.Image, box, colour, opacity: float, canvas: Image.Image) -> None:
    """Кладёт серую маску как чернила: умножением, чтобы бумага просвечивала."""
    left, top, right, bottom = box
    mask = mask.resize((right - left, bottom - top), Image.LANCZOS)
    arr = np.asarray(mask, dtype=np.float32) / 255.0 * opacity
    region = np.asarray(canvas.crop(box), dtype=np.float32)
    tint = np.asarray(colour, dtype=np.float32)
    blended = region * (1 - arr[..., None]) + tint * arr[..., None]
    canvas.paste(Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8)), box)


def _place_animal(canvas: Image.Image, path: pathlib.Path) -> None:
    """Иллюстрация ложится умножением: свой пергамент у неё подтягивается к белому,
    иначе фон лёг бы вторым слоем и прямоугольник стал бы виден швом."""
    left, top, right, bottom = ANIMAL_BOX
    img = Image.open(path).convert("L").resize((right - left, bottom - top), Image.LANCZOS)
    arr = np.asarray(img, dtype=np.float32)
    paper = float(np.percentile(arr, 82)) or 255.0
    layer = Image.fromarray(
        np.clip(arr / paper * 255.0, 0, 255).astype(np.uint8)
    ).convert("RGB")

    region = canvas.crop(ANIMAL_BOX)
    blended = ImageChops.multiply(region, layer)
    feather = 34
    mask = Image.new("L", (right - left, bottom - top), 0)
    ImageDraw.Draw(mask).rectangle(
        [feather, feather, right - left - feather, bottom - top - feather], fill=255)
    canvas.paste(Image.composite(blended, region,
                                 mask.filter(ImageFilter.GaussianBlur(feather * 0.5))), ANIMAL_BOX)


def _draw_stamp(canvas: Image.Image, caption: str, kind: str) -> None:
    """Круглая печать: два кольца, текст по верхней дуге, силуэт тотема внутри."""
    cx, cy = STAMP_CENTER
    plate = Image.new("RGBA", (STAMP_R * 2 + 40,) * 2, (0, 0, 0, 0))
    d = ImageDraw.Draw(plate)
    o = STAMP_R + 20
    for r, width in ((STAMP_R, 5), (STAMP_R * 0.87, 2)):
        d.ellipse([o - r, o - r, o + r, o + r], outline=BLUE + (255,), width=int(width))

    sil = signs.stamp_silhouette(kind, int(STAMP_R * 1.05))
    plate.paste(Image.new("RGBA", sil.size, BLUE + (255,)), (o - sil.width // 2, o - sil.height // 2 + 8), sil)

    radius = STAMP_R * 0.74
    size = 30.0
    while size > 13:
        font = _font(round(size))
        need = sum(d.textlength(c, font=font) for c in caption) * 1.16
        if need <= radius * math.radians(250):
            break
        size *= 0.94
    font = _font(round(size))
    widths = [d.textlength(c, font=font) * 1.16 for c in caption]
    cursor = -math.pi / 2 - (sum(widths) / radius) / 2
    for ch, w in zip(caption, widths):
        cursor += (w / radius) / 2
        glyph = Image.new("RGBA", (72, 72), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((36, 36), ch, font=font, fill=BLUE + (255,), anchor="mm")
        glyph = glyph.rotate(-math.degrees(cursor) - 90, resample=Image.BICUBIC)
        plate.paste(glyph, (round(o + math.cos(cursor) * radius) - 36,
                            round(o + math.sin(cursor) * radius) - 36), glyph)
        cursor += (w / radius) / 2

    for i in range(5):
        a = math.pi / 2 + (i - 2) * math.radians(11)
        px, py = o + math.cos(a) * radius, o + math.sin(a) * radius
        ImageDraw.Draw(plate).ellipse([px - 4, py - 4, px + 4, py + 4], fill=BLUE + (255,))

    plate = plate.rotate(-7, resample=Image.BICUBIC)          # печать всегда чуть криво
    plate.putalpha(plate.getchannel("A").point(lambda v: int(v * 0.82)))
    canvas.paste(plate, (cx - o, cy - o), plate)


def bake(totem_id: str, cfg: dict) -> Image.Image:
    canvas = Image.open(TEMPLATE).convert("RGB")
    d = ImageDraw.Draw(canvas)
    p, geo = cfg["passport"], cfg["geo"]

    illustration = GENERATED / f"{totem_id}.png"
    if illustration.exists():
        _place_animal(canvas, illustration)

    # --- восемь значений; девятое, имя, печатает бот ---
    for (top, bottom), key in zip(VALUE_ROWS, FIELD_ORDER):
        value = p[key]
        font = _fitted(d, value, 1130 - 700, (bottom - top) * 1.62, 0.5)
        _tracked(d, (700, (top + bottom) / 2), value, font, INK, tracking=0.5, anchor="lm")

    # --- правая колонка; номер и код тоже за ботом, они у каждого свои ---
    for i, line in enumerate(p["motto"]):
        _tracked(d, (1503, 317 + i * 23), line, _font(19), INK_SOFT, tracking=1.0, anchor="mm")
    _draw_stamp(canvas, p["stamp"], cfg["track"])

    # координаты по правому краю, снизу вверх.
    # Ширину полосы считаем от текста: на фиксированных 300 px строка обрезалась.
    coord_font = _font(19)
    coord_w = round(sum(d.textlength(c, font=coord_font) for c in geo["coords"])
                    + 2.4 * (len(geo["coords"]) - 1)) + 20
    strip = Image.new("RGBA", (coord_w, 34), (0, 0, 0, 0))
    _tracked(ImageDraw.Draw(strip), (coord_w / 2, 17), geo["coords"], coord_font, INK_SOFT,
             tracking=2.4, anchor="mm")
    strip = strip.rotate(-90, expand=True, resample=Image.BICUBIC)
    canvas.paste(strip, (1878 - strip.width // 2, 535 - strip.height // 2), strip)

    # --- нижняя полоса ---
    d = ImageDraw.Draw(canvas)
    for y, text in ((840, geo["coords"]), (893, geo["elevation"]), (942, geo["wind"])):
        _tracked(d, (80, y), text, _font(20), INK_SOFT, tracking=0.6, anchor="lm")

    # Область тотема на карте: не ровный эллипс, а рыхлое пятно из нескольких
    # окружностей — ровный овал читается как маркер на гуглокарте, не как заливка
    # региона на старой печатной схеме.
    left, top, right, bottom = MAP_BOX
    sx, sy = geo["map_spot"]
    spot = (left + (right - left) * sx, top + (bottom - top) * sy)
    blob = Image.new("L", (140, 110), 0)
    bd = ImageDraw.Draw(blob)
    blob_rng = np.random.default_rng(zlib.crc32(totem_id.encode()))
    for _ in range(7):
        bx, by = 70 + blob_rng.normal(0, 16), 55 + blob_rng.normal(0, 11)
        rx, ry = blob_rng.uniform(20, 34), blob_rng.uniform(14, 24)
        bd.ellipse([bx - rx, by - ry, bx + rx, by + ry], fill=255)
    blob = blob.filter(ImageFilter.GaussianBlur(4))
    _ink_layer(blob, (round(spot[0]) - 70, round(spot[1]) - 55,
                      round(spot[0]) + 70, round(spot[1]) + 55),
               (138, 170, 192), 0.62, canvas)
    d = ImageDraw.Draw(canvas)
    _tracked(d, (spot[0], spot[1]), geo["region_label"], _font(16), INK,
             tracking=1.0, anchor="mm")

    _tracked(d, (790, 854), p["auth_sub"], _font(20), INK_SOFT, tracking=2.0, anchor="mm")
    _tracked(d, (1193, 854), p["biometric"], _font(20), INK_SOFT, tracking=2.0, anchor="mm")

    # след: четыре оттиска в ряд, каждый чуть сдвинут и повёрнут
    track_mask = signs.TRACKS[cfg["track"]]()
    seed = zlib.crc32(totem_id.encode())
    rng = np.random.default_rng(seed)
    tl, tt, tr, tb = TRACK_BOX
    step = (tr - tl) / 4
    for i in range(4):
        one = track_mask.rotate(rng.uniform(-9, 9), resample=Image.BICUBIC, fillcolor=0)
        cx = tl + step * (i + 0.5) + rng.uniform(-4, 4)
        half = min(step * 0.42, (tb - tt) * 0.5)
        box = (round(cx - half), round(tt + rng.uniform(0, 6)),
               round(cx + half), round(tt + rng.uniform(0, 6)) + (tb - tt) - 6)
        _ink_layer(one, box, INK, 0.72, canvas)

    texture = signs.TEXTURES[cfg["texture"]](
        TEXTURE_BOX[2] - TEXTURE_BOX[0], TEXTURE_BOX[3] - TEXTURE_BOX[1], seed)
    _ink_layer(texture, TEXTURE_BOX, INK_SOFT, 0.68, canvas)

    return canvas


SOURCE = ROOT / "content" / "totems.yaml"
STAMP = OUT / ".source-sha256"


def source_hash() -> str:
    """Отпечаток контента, из которого испечены бланки.

    Сравнивать даты файлов нельзя: git не сохраняет mtime, и после клона
    порядок произвольный — проверка ругалась бы на каждом свежем деплое.
    """
    return hashlib.sha256(SOURCE.read_bytes()).hexdigest()


def main() -> None:
    totems = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    for totem_id, cfg in totems.items():
        path = OUT / f"{totem_id}.png"
        bake(totem_id, cfg).save(path, optimize=True)
        print(f"  {totem_id:8} {path.relative_to(ROOT)}  {path.stat().st_size // 1024} КБ")
    STAMP.write_text(source_hash())
    print(f"\n{len(totems)} бланка(ов). Бот их только читает — перепекать после "
          f"правки content/totems.yaml.")


if __name__ == "__main__":
    main()
