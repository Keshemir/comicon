"""Отрисовка следов и фактур четырёх тотемов.

Геометрия — из веб-исследования, полный разбор в docs/signs-research.md.
Все функции рисуют в нормированном поле 100×100 и масштабируются вызывающим;
ось Y вниз, движение животного вверх.

Ключевые различия, по которым след узнаётся (и которые нельзя перепутать):
  копыто  — ОДИН замкнутый контур, пальцев нет, стрелка-V остриём ВПЕРЁД
  беркут  — не подушечка, а звезда: три пальца вперёд, один назад,
            когти отдельными крючками через разрыв
  тазы    — вытянутый овал, когти есть ВСЕГДА (псовые их не втягивают)
  ірбіс   — круглая розетка, когтей НЕТ ВООБЩЕ, пятка в 2/3 ширины следа
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

INK = (86, 72, 54)
INK_SOFT = (128, 112, 88)


def _field(size: int = 400) -> tuple[Image.Image, ImageDraw.ImageDraw, float]:
    img = Image.new("L", (size, size), 0)
    return img, ImageDraw.Draw(img), size / 100.0


def _ellipse(d, k, cx, cy, w, h, **kw) -> None:
    d.ellipse([(cx - w / 2) * k, (cy - h / 2) * k, (cx + w / 2) * k, (cy + h / 2) * k], **kw)


# --- следы -----------------------------------------------------------------

def hoof(size: int = 400) -> Image.Image:
    """Копыто. Единый контур, кайма роговой стенки, стрелка остриём вперёд."""
    img, d, k = _field(size)
    _ellipse(d, k, 50, 60, 74, 78, fill=210)                 # общий отпечаток
    _ellipse(d, k, 50, 61, 58, 62, fill=90)                  # подошва светлее каймы
    # стрелка: V остриём вверх, площадь около четверти следа
    d.polygon([(50 * k, 46 * k), (63 * k, 88 * k), (56 * k, 88 * k),
               (50 * k, 62 * k), (44 * k, 88 * k), (37 * k, 88 * k)], fill=235)
    # заворотные части — два прямых штриха от пяточных углов внутрь
    for x0, x1 in ((26, 40), (74, 60)):
        d.line([x0 * k, 80 * k, x1 * k, 60 * k], fill=190, width=max(int(2 * k), 1))
    d.rectangle([0, 88 * k, size, size], fill=0)             # пятка срезана
    return img


def talon(size: int = 400) -> Image.Image:
    """Беркут. Три пальца вперёд, один назад; когти — отдельные крючки."""
    img, d, k = _field(size)
    hub = (50, 60)
    # (конец пальца, толщина, число мозолей, длина когтя)
    toes = [((50, 2), 7.0, 3, 11), ((16, 24), 6.0, 2, 7),
            ((84, 28), 6.0, 4, 7), ((50, 98), 6.5, 1, 15)]
    for (tx, ty), width, beads, claw in toes:
        d.line([hub[0] * k, hub[1] * k, tx * k, ty * k], fill=205,
               width=max(int(width * k), 2))
        for i in range(beads):                                # бугры-«чётки»
            t = (i + 1) / (beads + 1)
            _ellipse(d, k, hub[0] + (tx - hub[0]) * t, hub[1] + (ty - hub[1]) * t,
                     width * 1.5, width * 1.5, fill=235)
        # коготь: отдельная ямка ВПЕРЕДИ кончика, через разрыв
        vx, vy = tx - hub[0], ty - hub[1]
        norm = math.hypot(vx, vy) or 1
        gx, gy = vx / norm, vy / norm
        d.line([(tx + gx * 3) * k, (ty + gy * 3) * k,
                (tx + gx * (3 + claw)) * k, (ty + gy * (3 + claw)) * k],
               fill=250, width=max(int(3.4 * k), 2))
    _ellipse(d, k, *hub, 9, 9, fill=150)                      # пятка почти не читается
    return img


def paw_claw(size: int = 400) -> Image.Image:
    """Тазы. Вытянутый овал, пальцы-овалы 2:1, когти обязательны."""
    img, d, k = _field(size)
    _ellipse(d, k, 50, 80, 40, 33, fill=215)                  # пяточная подушка
    d.polygon([(42 * k, 96 * k), (50 * k, 88 * k), (58 * k, 96 * k)], fill=0)  # выемка
    toes = [(41, 30), (59, 30), (26, 45), (74, 45)]           # два средних выдвинуты
    for cx, cy in toes:
        _ellipse(d, k, cx, cy, 16, 28, fill=215)
        vx, vy = cx - 50, cy - 62
        norm = math.hypot(vx, vy) or 1
        d.line([(cx + vx / norm * 13) * k, (cy + vy / norm * 13) * k,
                (cx + vx / norm * 20) * k, (cy + vy / norm * 20) * k],
               fill=250, width=max(int(2.6 * k), 2))
    return img


def paw_soft(size: int = 400) -> Image.Image:
    """Ірбіс. Круглая розетка, огромная трёхдольчатая пятка, когтей НЕТ."""
    img, d, k = _field(size)
    _ellipse(d, k, 50, 74, 65, 30, fill=215)                  # пятка в 2/3 ширины
    for x in (36, 64):                                        # задний край трёхдольчатый
        d.line([x * k, 84 * k, x * k, 92 * k], fill=0, width=max(int(3.4 * k), 2))
    for cx, cy in ((38, 36), (62, 36), (22, 52), (78, 52)):
        _ellipse(d, k, cx, cy, 23, 24, fill=215)              # пальцы круглее, чем у тазы
    return img


# --- фактуры ---------------------------------------------------------------

def hair(w: int, h: int, seed: int) -> Image.Image:
    """Конский волос: жгуты почти параллельных линий с рваными концами."""
    img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng(seed)
    for clump in range(8):                                    # 5–9 локонов
        base = h * (clump + 0.5) / 8 + rng.normal(0, h * 0.02)
        for _ in range(rng.integers(6, 11)):
            y0 = base + rng.normal(0, h * 0.035)
            amp, ph = rng.uniform(h * 0.02, h * 0.06), rng.uniform(0, math.tau)
            end = rng.uniform(0.72, 1.0)                      # концы обрываются вразнобой
            pts = []
            for t in np.linspace(0, end, 48):
                pts += [t * w, y0 + math.sin(ph + t * 5.0) * amp * t + t * h * 0.05]
            d.line(pts, fill=int(rng.integers(120, 230)), width=1 + (rng.random() < 0.12))
    return img


def feather(w: int, h: int, seed: int) -> Image.Image:
    """Перо: стержень и бородки под постоянным углом, опахало асимметрично."""
    img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng(seed)
    for shaft in range(3):
        y0 = h * (shaft + 0.5) / 3
        pts = [(t * w, y0 + math.sin(t * 2.2) * h * 0.06) for t in np.linspace(0, 1, 40)]
        d.line([c for p in pts for c in p], fill=225, width=2)
        for i, (px, py) in enumerate(pts[:-1]):
            if i % 1:
                continue
            for side, length in ((-1, h * 0.16), (1, h * 0.10)):   # верх длиннее низа
                fade = 1 - i / len(pts) * 0.45
                d.line([px, py, px + length * 0.55, py + side * length * fade],
                       fill=int(150 + rng.integers(0, 70)), width=1)
    return img


def fringe(w: int, h: int, seed: int) -> Image.Image:
    """Бурка тазы: мягкая волнистая бахрома, пряди разной длины."""
    img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng(seed)
    for _ in range(110):
        x0 = rng.uniform(0, w)
        length = rng.uniform(h * 0.35, h * 0.95)
        drift, amp = rng.uniform(-0.22, 0.22), rng.uniform(h * 0.03, h * 0.09)
        ph = rng.uniform(0, math.tau)
        pts = []
        for t in np.linspace(0, 1, 30):
            pts += [x0 + drift * length * t + math.sin(ph + t * 3.4) * amp * t,
                    t * length]
        d.line(pts, fill=int(rng.integers(110, 220)), width=1)
    return img


def rosette(w: int, h: int, seed: int) -> Image.Image:
    """Барс: незамкнутые кольца-розетки, сплошные мелкие пятна по краям."""
    img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng(seed)
    for _ in range(26):
        cx, cy = rng.uniform(w * 0.10, w * 0.90), rng.uniform(h * 0.18, h * 0.82)
        r = rng.uniform(h * 0.10, h * 0.19)
        for arc in range(int(rng.integers(3, 6))):            # кольцо разомкнуто
            a0 = arc * 360 / 4 + rng.uniform(-14, 14)
            d.arc([cx - r, cy - r, cx + r, cy + r], a0, a0 + rng.uniform(45, 72),
                  fill=int(rng.integers(150, 230)), width=2)
        if rng.random() < 0.55:                               # ядро розетки
            rr = r * 0.24
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=170)
    for _ in range(90):                                       # мелкие сплошные пятна
        cx, cy = rng.uniform(0, w), rng.uniform(0, h)
        rr = rng.uniform(1.2, 2.6)
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=int(rng.integers(160, 235)))
    return img


TRACKS = {"hoof": hoof, "talon": talon, "paw_claw": paw_claw, "paw_soft": paw_soft}
TEXTURES = {"hair": hair, "feather": feather, "fringe": fringe, "rosette": rosette}


def stamp_silhouette(kind: str, size: int) -> Image.Image:
    """Силуэт животного внутрь круглой печати.

    Рисовать полигонами вручную не вышло — орёл получался звездой. Силуэты
    сгенерированы отдельно (tools/gen_assets.py) как чёрное на белом; здесь
    инвертируем в маску и обрезаем по фактическим границам фигуры.
    """
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[2] / "assets" / "generated" / f"sil_{kind}.png"
    if not path.exists():
        return Image.new("L", (size, size), 0)

    mask = Image.open(path).convert("L").point(lambda v: 255 if v < 128 else 0)
    bbox = mask.getbbox()
    if bbox:
        mask = mask.crop(bbox)

    # вписываем в квадрат, сохраняя пропорции: конь шире, чем высок
    scale = size / max(mask.width, mask.height)
    mask = mask.resize((max(round(mask.width * scale), 1), max(round(mask.height * scale), 1)),
                       Image.LANCZOS)
    plate = Image.new("L", (size, size), 0)
    plate.paste(mask, ((size - mask.width) // 2, (size - mask.height) // 2))
    return plate.filter(ImageFilter.GaussianBlur(size / 100.0 * 0.35))
