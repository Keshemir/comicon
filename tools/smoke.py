#!/usr/bin/env python3
"""Прогон всего пайплайна без Telegram: квиз → имя → лицо → кадр → паспорт.

Токен бота не нужен. Полезно перед деплоем и после правки контента.

    python3 tools/smoke.py
"""

import asyncio
import hashlib
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
    for totem in totems:
        if not (ROOT / "assets" / "templates" / f"{totem}.png").exists():
            failures += not check(f"бланк {totem} — запусти tools/bake_templates.py", False)
        if not (ROOT / "assets" / "generated" / f"{totem}.png").exists():
            failures += not check(f"иллюстрация {totem}", False)

    # Главный способ выстрелить себе в ногу: поправить totems.yaml и забыть
    # перепечь бланки. Сверяем отпечаток контента, а НЕ даты файлов: git не
    # сохраняет mtime, и после клона проверка по датам врала бы на каждом
    # свежем деплое.
    stamp = ROOT / "assets" / "templates" / ".source-sha256"
    want = hashlib.sha256((config.CONTENT / "totems.yaml").read_bytes()).hexdigest()
    fresh = stamp.exists() and stamp.read_text().strip() == want
    failures += not check("бланки испечены из текущего totems.yaml", fresh,
                          "" if fresh else "перепеки: python3 tools/bake_templates.py")

    print("\nтексты")
    i18n = yaml.safe_load((config.CONTENT / "i18n.yaml").read_text(encoding="utf-8"))
    # Ключей три языка × два десятка, и забытый вылезет только на стенде —
    # в t() он молча подменится русским, а гость получит чужой язык.
    base = set(i18n["ru"])
    for lang, block in i18n.items():
        gap = sorted(base - set(block))
        failures += not check(f"ключи {lang} на месте", not gap, ", ".join(gap))

    print("\nквиз")
    failures += not check("вопросов ровно 4", len(quiz.QUESTIONS) == 4,
                          f"их {len(quiz.QUESTIONS)}")

    # Telegram обрезает подпись инлайн-кнопки по ширине экрана и дорисовывает
    # многоточие. На узком телефоне кириллица уезжает после ~36 знаков, так что
    # держим потолок с запасом — иначе гость не дочитает вариант ответа.
    long_opts = [f"{q['id']}/{lang} {len(t)}" for q in quiz.QUESTIONS for o in q["options"]
                 for lang, t in o["text"].items() if len(t) > 32]
    failures += not check("варианты влезают в кнопку", not long_opts,
                          "длиннее 32 знаков: " + ", ".join(long_opts) if long_opts else "")
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

    print("\nреакция на гостя")
    # Каждое сообщение гостя должно получить ответ. Молчащий бот на стенде
    # читается как сломанный, а человек уходит, не нажав ни одной кнопки.
    from bot.handlers import flow                                 # noqa: PLC0415

    class FakeMessage:
        def __init__(self, uid, text=None):
            self.from_user = type("U", (), {"id": uid})()
            self.text, self.replies = text, []

        async def answer(self, text, **kw):
            self.replies.append(text)

    async def reacts(uid, state, handler, **kw):
        flow.SESSIONS.clear()
        if state:
            flow.SESSIONS[uid] = state
        msg = FakeMessage(uid, kw.pop("text", None))
        await handler(msg, **kw)
        return msg.replies

    done = quiz.Session(started=True, name="ZHANIBEK", step=len(quiz.QUESTIONS), scores={})
    cases = [
        ("текст без сессии", await reacts(-11, None, flow.on_name, db=db, text="привет")),
        ("файл без сессии", await reacts(-12, None, flow.on_not_photo)),
        ("фото без сессии", await reacts(-13, None, flow.on_photo, db=db, bot=None)),
        ("текст вместо фото", await reacts(-14, done, flow.on_name, db=db, text="а как?")),
    ]
    for label, replies in cases:
        failures += not check(label, bool(replies),
                              "молчит" if not replies else replies[0].splitlines()[0][:46])
    flow.SESSIONS.clear()

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
            await db.close()
            print(f"\nпровалов: {failures + 1}\n")
            return 1

        blank = io.BytesIO()
        photo.sepia(portrait).save(blank, "PNG")
        try:
            photo.find_face(b"not-an-image")
            failures += not check("мусор отвергается", False)
        except photo.NoFace:
            failures += not check("мусор отвергается", True)

        print("\nсборка паспорта")
        card = compose.render_from_bytes(
            compose.Passport(name=latin, serial=serial, code=code, totem_id="at"),
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
