#!/usr/bin/env python3
"""Прогон всего пайплайна без Telegram: квиз → имя → лицо → кадр → паспорт.

Токен бота не нужен. Полезно перед деплоем и после правки контента.

    python3 tools/smoke.py
"""

import asyncio
import io
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bot import config, storage                                    # noqa: E402
from bot.render import compose                                     # noqa: E402
from bot.services import names, photo, quiz                        # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "out"


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
    return ok


async def main() -> int:
    failures = 0
    totems = yaml.safe_load((config.CONTENT / "totems.yaml").read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)

    print("\nконфигурация")
    gaps = config.missing()
    check("ключи на месте", not gaps, "; ".join(gaps))
    check("бланк собран", (ROOT / "assets" / "source" / "template.png").exists())
    for totem in totems:
        if not (ROOT / "assets" / "generated" / f"{totem}.png").exists():
            failures += not check(f"иллюстрация {totem}", False)

    print("\nквиз")
    failures += not check("вопросов ровно 4", len(quiz.QUESTIONS) == 4,
                          f"их {len(quiz.QUESTIONS)}")
    reached = set()
    for uid in range(400):
        state = quiz.Session()
        for step in range(len(quiz.QUESTIONS)):
            state.answer(uid // (4 ** step) % 4)
        reached.add(quiz.resolve(state.scores, uid))
    missing_totems = set(totems) - reached
    failures += not check("все четыре тотема достижимы", not missing_totems,
                          f"недостижимы: {sorted(missing_totems)}" if missing_totems else "")

    state = quiz.Session()
    for _ in range(len(quiz.QUESTIONS)):
        state.answer(0)
    first = quiz.resolve(state.scores, 777)
    failures += not check("результат стабилен при повторе",
                          all(quiz.resolve(state.scores, 777) == first for _ in range(5)))

    print("\nхранилище")
    db = await storage.connect()
    await storage.reset_user(db, -1)
    serial, code = await storage.next_serial(db)
    failures += not check("серийный номер выдаётся", bool(serial and code), f"{serial} / {code}")

    print("\nимена")
    latin, meaning, live = await names.turkify(db, "Johny")
    failures += not check("Johny тюркизирован", latin.isalpha() and latin.isupper(),
                          f"{latin} — {meaning or 'без значения'}"
                          + ("" if live else "  (запасной набор: Gemini недоступен)"))
    again, _, _ = await names.turkify(db, "Johny")
    failures += not check("кеш отдаёт то же самое", again == latin)
    failures += not check("пустой ввод не роняет", bool(names.fallback_for("")))

    print("\nфото")
    src = ROOT / "assets" / "generated" / "face_at.png"
    if not src.exists():
        failures += not check("нет тестового лица — запусти tools/gen_assets.py", False)
    else:
        raw = src.read_bytes()
        try:
            face = photo.find_face(raw)
            failures += not check("лицо найдено", True, f"рамка {face[2]}×{face[3]}")
            portrait = photo.crop_to_portrait(raw, face)
            failures += not check("кадр обрезан", portrait.size == (1024, 1024))
            failures += not check("локальная сепия работает",
                                  photo.sepia(portrait).size == portrait.size)
        except photo.NoFace as exc:
            failures += not check("лицо найдено", False, str(exc))

        blank = io.BytesIO()
        photo.sepia(portrait).save(blank, "PNG")
        try:
            photo.find_face(b"not-an-image")
            failures += not check("мусор отвергается", False)
        except photo.NoFace:
            failures += not check("мусор отвергается", True)

        print("\nсборка паспорта")
        cfg = totems["at"]
        card = compose.render_from_bytes(
            compose.Passport(name=latin, serial=serial, code=code, totem_id="at",
                             passport=cfg["passport"], geo=cfg["geo"],
                             track=cfg["track"], texture=cfg["texture"]),
            blank.getvalue(),
        )
        path = OUT / "smoke.png"
        card.save(path)
        failures += not check("паспорт собран", card.size == (1920, 1086),
                              f"{path.relative_to(ROOT)}")

    await db.close()
    print(f"\n{'всё зелёное' if not failures else f'провалов: {failures}'}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
