#!/usr/bin/env python3
"""Разборка макета на чистый шаблон.

Из assets/source/mockup.jpg вычищается всё, что меняется от паспорта к паспорту:
конь, беркут в шапке, печать, весь текст, содержимое нижней полосы. Остаётся
бланк — бумага, рамка, уголки, флаг, золотая печать, иконки полей, контур карты.

Два способа, каждый под свою задачу:

  erase   — морфологическое расширение светлого. Текст всегда темнее бумаги,
            поэтому MaxFilter съедает штрихи и оставляет фактуру. Донор не
            нужен — а его в этом макете и негде взять, плотная вёрстка.
  inpaint — OpenAI по маске. Нужен там, где под объектом рисунок, который надо
            восстановить: конь стоит на траве, беркут на завитках ветра.
            Результат кешируется, повторный запуск не тратит деньги.

    python3 tools/clean_template.py              # inpaint из кеша, если есть
    python3 tools/clean_template.py --refresh    # заново дёрнуть API
"""

import argparse
import base64
import io
import pathlib
import re
import sys

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "source" / "mockup.jpg"
OUT = ROOT / "assets" / "source" / "template.png"
CACHE = ROOT / "assets" / "source" / "cache"
EDIT = "https://api.openai.com/v1/images/edits"

# Стираем ТОЛЬКО то, что меняется от паспорта к паспорту. Подписи полей,
# заголовок, цитаты, пословица и дисклеймер остаются родной графикой макета —
# их не перерисовать тем же шрифтом и той же печатной фактурой.
#
# Значения полей идут строго под подписями; девять полос ниже — это они.
# (имя, бокс, радиус морфологии)
# Замерены по исходнику: колонка x 700..940 (уже травы, чтобы она не сливалась
# со строками), поиск строк с тёмными пикселями. На глаз я промахивался на
# 6-10 px и оставлял верхушки букв — они и читались призраком.
# Правый край замерен построчно. Стирать до общей границы нельзя: справа от
# коротких значений лежит нарисованная трава, и ровный срез по 1120 оставлял
# на ней вертикальную ступеньку. Длинное новое значение просто ляжет поверх
# травы — ровно так же, как в оригинале лежало «AS LONG AS THE WIND BLOWS».
# (верх, низ, правый край)
VALUE_BANDS = [
    (266, 306, 1000), (337, 368, 1043), (400, 431, 799), (460, 493, 781),
    (521, 555, 848), (581, 612, 1055), (640, 671, 895), (699, 730, 876),
    (758, 789, 1112),
]

ERASE = [
    *[(f"значение поля {i + 1}", (693, y0, x1, y1), 13)
      for i, (y0, y1, x1) in enumerate(VALUE_BANDS)],
    ("номер паспорта", (1624, 66, 1806, 96), 11),
    ("код SPP-KZ", (136, 156, 322, 178), 9),   # только строка, флаг выше — не задеть
    ("девиз", (1422, 306, 1584, 376), 9),
    ("координаты справа", (1854, 386, 1902, 684), 9),
    # нижняя полоса: значения под подписями, сами подписи остаются
    ("координаты внизу", (74, 830, 320, 850), 7),
    ("высота", (74, 883, 320, 903), 7),
    ("роза ветров", (74, 933, 320, 952), 7),
    ("подпись региона", (468, 860, 584, 898), 9),
    # в блоках нижней полосы: вторая строка подписи и сам рисунок
    ("след: подпись", (630, 842, 950, 866), 7),
    ("след: рисунок", (630, 866, 950, 954), 15),
    ("биометрия: подпись", (996, 842, 1390, 866), 7),
    ("биометрия: рисунок", (996, 866, 1390, 954), 15),
    # Тамгу НЕ трогаем: родовых знаков у беркута, тазы и барса не существует,
    # а выдумывать их на документе с казахской символикой — заметят. Оставляем
    # знак макета одинаковым на всех четырёх. См. docs/signs-research.md.
]

# Крупные объекты: под ними рисунок, морфология тут не спасёт.
INPAINTS = [
    {
        "name": "horse",
        "crop": (880, 270, 1660, 740),   # рамка шире объекта: модели нужен контекст
        "mask": [(1120, 330, 1600, 700)],
        # Кадр захватывает колонку полей, а API пересжимает его целиком —
        # подписи и линейки надо вернуть из оригинала нетронутыми.
        "protect": [(600, 260, 1130, 750)],
        "prompt": (
            "Remove the horse completely. Fill the area with the same faint sepia pencil "
            "landscape that surrounds it: distant sketchy mountains along the horizon, "
            "dry steppe grass tufts in the foreground, and pale aged parchment above. "
            "Match the existing line weight, sepia tone and paper texture exactly. "
            "No animal, no figure, no text, no new objects — only empty landscape."
        ),
    },
    {
        "name": "eagle",
        "crop": (1400, 40, 1700, 260),
        "mask": [(1452, 74, 1610, 212)],
        # Хвост заголовка попадает в кадр — без защиты модель переписала
        # последнюю «E» в «P», получилось CITIZEN OF THE STEPPP.
        "protect": [(1400, 40, 1500, 180)],
        "prompt": (
            "Remove the golden eagle completely. Fill the area with plain aged cream "
            "parchment matching the surrounding paper, continuing the very faint sepia "
            "swirl line-work that runs through this region. "
            "No bird, no gold, no text, no new objects."
        ),
    },
    {
        "name": "stamp",
        "crop": (1520, 330, 1900, 700),
        "mask": [(1596, 396, 1836, 636)],
        "prompt": (
            "Remove the blue circular stamp completely. Fill the area with plain aged "
            "cream parchment matching the surrounding paper, continuing the faint sepia "
            "texture. No stamp, no circle, no blue ink, no text, no new objects."
        ),
    },
]


def load_key() -> str:
    m = re.search(r"^OPENAI_API_KEY=(.+)$", (ROOT / ".env").read_text(), re.M)
    if not m or not m.group(1).strip():
        sys.exit("OPENAI_API_KEY не найден в .env")
    return m.group(1).strip()


def erase_text(img: Image.Image, box, radius: int, feather: int = 5) -> None:
    """Подменяет область синтезированной бумагой.

    Фильтрацией текст отсюда не убрать: надписи на макете набраны с тиснением,
    у них есть и тёмный штрих, и светлый ореол — MaxFilter снимает тёмное и
    оставляет призрак. Клонировать чужой кусок бланка тоже нельзя: свободного
    пергамента в этой вёрстке нет, и вместе с бумагой приезжают горы и завитки.

    Поэтому фактуру синтезируем по локальной статистике: берём медианный тон и
    разброс с кольца вокруг области и генерируем шум с теми же параметрами.
    На масштабе строки текста это неотличимо от настоящей бумаги.
    """
    # Растушёвка идёт НАРУЖУ от запрошенной области: если размывать край внутрь,
    # переходная зона садится ровно на буквы и они переживают замену.
    inner = box
    left = max(inner[0] - feather * 2, 0)
    top = max(inner[1] - feather * 2, 0)
    right = min(inner[2] + feather * 2, img.width)
    bottom = min(inner[3] + feather * 2, img.height)
    box = (left, top, right, bottom)
    width, height = right - left, bottom - top
    rng = np.random.default_rng(left * 131 + top)

    # Кольцо вокруг области — эталон бумаги именно в этом месте бланка.
    # Берём медиану, а не среднее: среднее утащат вниз тёмные хвосты соседних букв.
    pad = 14
    outer = (max(left - pad, 0), max(top - pad, 0),
             min(right + pad, img.width), min(bottom + pad, img.height))
    ring = np.asarray(img.crop(outer), dtype=np.float32)
    ring_mask = np.ones(ring.shape[:2], dtype=bool)
    ring_mask[pad:-pad or None, pad:-pad or None] = False
    sample = ring[ring_mask].reshape(-1, 3) if ring_mask.any() else ring.reshape(-1, 3)
    tone = np.median(sample, axis=0)
    spread = float(np.clip(np.median(np.abs(sample - tone)) * 1.4, 2.0, 9.0))

    patch = np.repeat(np.repeat(tone[None, None, :], height, 0), width, 1).astype(np.float32)
    patch += rng.normal(0, spread, (height, width, 1))          # зерно бумаги
    # Крупные разводы держим слабыми: на такой площади они читаются пятнами,
    # а не фактурой, и заплата начинает выделяться сильнее самого текста.
    low = rng.normal(0, spread * 0.30, (max(height // 6, 2), max(width // 6, 2), 1))
    low = np.asarray(Image.fromarray(
        np.clip(low[..., 0] + 128, 0, 255).astype(np.uint8)
    ).resize((width, height), Image.BICUBIC), dtype=np.float32) - 128
    patch += low[..., None]

    piece_img = Image.fromarray(np.clip(patch, 0, 255).astype(np.uint8))
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rectangle(
        [inner[0] - left, inner[1] - top, inner[2] - left - 1, inner[3] - top - 1], fill=255
    )
    img.paste(Image.composite(piece_img, img.crop(box),
                              mask.filter(ImageFilter.GaussianBlur(feather * 0.8))), box)


def inpaint(img: Image.Image, spec: dict, key: str | None, refresh: bool,
            original: Image.Image | None = None) -> None:
    """Стирает объект через OpenAI. Работает с вырезанным фрагментом.

    Целиком бланк в API не отправить: поддерживаются лишь фиксированные размеры,
    и весь макет пришлось бы пересжать. Фрагмент режем сами и вклеиваем назад.
    """
    left, top, right, bottom = spec["crop"]
    cached = CACHE / f"{spec['name']}.png"

    def restore_protected() -> None:
        """Возвращает нетронутые куски из оригинала.

        API пересжимает ВЕСЬ переданный кадр, а не только область под маской,
        поэтому соседняя графика внутри кадра слегка «переписывается».
        """
        if not original:
            return
        for box in spec.get("protect", []):
            img.paste(original.crop(box), (box[0], box[1]))

    if cached.exists() and not refresh:
        img.paste(Image.open(cached).convert("RGB").resize((right - left, bottom - top),
                                                           Image.LANCZOS), (left, top))
        restore_protected()
        print(f"  {spec['name']:10} из кеша")
        return

    crop = img.crop(spec["crop"]).resize((1536, 1024), Image.LANCZOS)
    sx, sy = 1536 / (right - left), 1024 / (bottom - top)
    mask = Image.new("RGBA", (1536, 1024), (255, 255, 255, 255))
    md = ImageDraw.Draw(mask)
    for mx0, my0, mx1, my1 in spec["mask"]:
        md.rectangle([(mx0 - left) * sx, (my0 - top) * sy,
                      (mx1 - left) * sx, (my1 - top) * sy], fill=(0, 0, 0, 0))

    buf_img, buf_mask = io.BytesIO(), io.BytesIO()
    crop.save(buf_img, "PNG")
    mask.save(buf_mask, "PNG")

    resp = httpx.post(
        EDIT,
        headers={"Authorization": f"Bearer {key}"},
        files={"image": ("crop.png", buf_img.getvalue(), "image/png"),
               "mask": ("mask.png", buf_mask.getvalue(), "image/png")},
        data={"model": "gpt-image-2", "prompt": spec["prompt"], "size": "1536x1024", "n": "1"},
        timeout=300,
    )
    if resp.status_code != 200:
        print(f"  {spec['name']:10} ОШИБКА {resp.status_code} {resp.text[:180]}")
        return

    body = resp.json()
    result = Image.open(io.BytesIO(base64.b64decode(body["data"][0]["b64_json"]))).convert("RGB")
    CACHE.mkdir(parents=True, exist_ok=True)
    result.save(cached)
    img.paste(result.resize((right - left, bottom - top), Image.LANCZOS), (left, top))
    restore_protected()

    usage = body.get("usage", {})
    detail = usage.get("input_tokens_details", {})
    cost = (detail.get("text_tokens", 0) / 1e6 * 5 + detail.get("image_tokens", 0) / 1e6 * 8
            + usage.get("output_tokens", 0) / 1e6 * 30)
    print(f"  {spec['name']:10} стёрт  ${cost:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="перегенерировать inpaint")
    args = parser.parse_args()

    img = Image.open(SRC).convert("RGB")
    original = img.copy()

    print("inpaint крупных объектов:")
    for spec in INPAINTS:
        need_key = args.refresh or not (CACHE / f"{spec['name']}.png").exists()
        inpaint(img, spec, load_key() if need_key else None, args.refresh, original)

    print("подмена бумагой:")
    for name, box, radius in ERASE:
        erase_text(img, box, radius)
        print(f"  {name}")

    # фото просто зачищаем в тон рамки — сверху всегда ляжет портрет гостя
    img.paste(Image.new("RGB", (492, 596), (206, 196, 172)), (117, 195))

    img.save(OUT)
    print(f"\nшаблон: {OUT.relative_to(ROOT)}  {img.size[0]}×{img.size[1]}")


if __name__ == "__main__":
    main()
