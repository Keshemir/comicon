#!/usr/bin/env python3
"""Демо-рендер: по паспорту на каждый тотем поверх настоящего бланка.

    python3 tools/render_demo.py
"""

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bot.render.compose import Passport, render  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "out"

DEMO = {
    "at": ("ZHANIBEK", "000122024", "0001"),
    "berkut": ("QUDAIBERGEN", "000122025", "0002"),
    "tazy": ("MARIYAM", "000122026", "0003"),
    "irbis": ("BORAN", "000122027", "0004"),
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    started = time.time()

    for totem, (name, serial, code) in DEMO.items():
        data = Passport(name=name, serial=serial, code=code, totem_id=totem)
        photo = ROOT / "assets" / "generated" / f"styled_{totem}.png"
        img = render(data, photo if photo.exists() else None)
        path = OUT / f"passport_{totem}.png"
        img.save(path, optimize=True)
        print(f"  {totem:8} {name:14} {img.size[0]}×{img.size[1]}  {path.stat().st_size // 1024} КБ")

    print(f"4 паспорта за {time.time() - started:.1f} c → {OUT}")


if __name__ == "__main__":
    main()
