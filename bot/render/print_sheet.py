"""Шаг 3: приведение разворота к печатному листу.

Числа сняты с реального паспорт-холдера, не выведены из пропорций:

    закрытый размер          88 × 125 мм
    полный разворот         125 × 176 мм
    окошко верхней створки  122 × 75 мм (кожаная рамка перекрывает края)
    зона сгиба              ~10–15 мм по центру, не видна вообще
    нижняя створка          карман, низ листа частично скрыт

Отсюда три операции: центр-кроп до 125:176, ресайз до 300 DPI и сдвиг всего
листа на 6 мм вверх. Сдвиг — калибровка под холдер: без него в окошке над
документом видна пустая полоса бумаги. Снизу дозаливается зеркальная полоса
собственной текстуры — шов не виден, там затухающая трава и бумага.

Эталон — ImageMagick-рецепт из ТЗ; здесь тот же результат на Pillow, чтобы не
тащить на сервер вторую графическую зависимость.
"""

from __future__ import annotations

import io

from PIL import Image

DPI = 300
_MM = 25.4

# (ширина, высота) в миллиметрах
MAIN_MM = (125.0, 176.0)
BLEED_MM = (129.0, 180.0)

# ТЗ называет второй файл «bleed3mm», но его же размеры дают 4 мм прибавки по
# каждой стороне, то есть 2 мм на край. Считаем вылет ОТ ГЕОМЕТРИИ и подписываем
# им и файл, и сообщение: разъехавшийся ярлык на печати стоит дороже спора о
# названии. Нужен настоящий вылет 3 мм — это 131×182, поправить BLEED_MM.
BLEED_PER_SIDE_MM = (BLEED_MM[0] - MAIN_MM[0]) / 2

SHIFT_MM = 6.0                     # вверх, калибровка под окошко холдера


def px(mm: float) -> int:
    """Миллиметры в пиксели при 300 DPI. 125 мм → 1476, 176 мм → 2079, 6 мм → 71."""
    return round(mm / _MM * DPI)


def _crop_to_ratio(img: Image.Image, ratio: float) -> Image.Image:
    """Центр-кроп до пропорции ширина/высота.

    ТЗ режет только по ширине — так и выходит, когда модель отдала 3:4 (шире
    целевых 125:176). Но если провайдер умеет лишь 2:3 или 9:16, картинка
    оказывается УЖЕ цели, и резать надо по высоте. Иначе кроп вылезает за
    границы кадра и Pillow молча дополняет его чёрным.
    """
    w, h = img.size
    if w / h > ratio:
        new_w = round(h * ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    new_h = round(w / ratio)
    top = (h - new_h) // 2
    return img.crop((0, top, w, top + new_h))


def _shift_up(sheet: Image.Image, shift: int) -> Image.Image:
    """Сдвигает лист вверх на shift px, низ дозаливает зеркалом собственного низа."""
    w, h = sheet.size
    if shift <= 0:
        return sheet
    out = Image.new("RGB", (w, h))
    out.paste(sheet.crop((0, shift, w, h)), (0, 0))
    tail = sheet.crop((0, h - shift, w, h)).transpose(Image.FLIP_TOP_BOTTOM)
    out.paste(tail, (0, h - shift))
    return out


def sheet(img: Image.Image, size_mm: tuple[float, float] = MAIN_MM) -> Image.Image:
    """Из результата шага 2 — готовый печатный лист. Работает с любым входным W×H."""
    width, height = px(size_mm[0]), px(size_mm[1])
    cropped = _crop_to_ratio(img.convert("RGB"), size_mm[0] / size_mm[1])
    return _shift_up(cropped.resize((width, height), Image.LANCZOS), px(SHIFT_MM))


def to_png(img: Image.Image) -> bytes:
    """PNG с метаданными 300 DPI — типография читает их из файла."""
    buf = io.BytesIO()
    img.save(buf, "PNG", dpi=(DPI, DPI), optimize=True)
    return buf.getvalue()


def filename(serial: str, size_mm: tuple[float, float], bleed: bool = False) -> str:
    tail = f"_bleed{BLEED_PER_SIDE_MM:g}mm" if bleed else ""
    return f"steppe_{serial}_{size_mm[0]:g}x{size_mm[1]:g}mm{tail}_{DPI}dpi.png"
