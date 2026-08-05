"""Основной сценарий: язык → имя → квиз → фото → паспорт."""

from __future__ import annotations

import asyncio
import html
import io
import logging

import yaml
from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart

from .. import config, storage
from ..render import compose, print_sheet
from ..services import names, photo, quiz, spread

log = logging.getLogger(__name__)
router = Router()

I18N = yaml.safe_load((config.CONTENT / "i18n.yaml").read_text(encoding="utf-8"))
TOTEMS = yaml.safe_load((config.CONTENT / "totems.yaml").read_text(encoding="utf-8"))

# Сессии живут в памяти: гость проходит квиз за минуту, переживать рестарт
# бота этому состоянию не нужно. В БД попадает только выданный паспорт.
SESSIONS: dict[int, quiz.Session] = {}

# Кто прямо сейчас в рендере. Лимит проверяется в начале, а пишется в конце —
# между ними ~40 секунд стилизации, и два фото подряд проскакивали оба.
# ponytail: множество на процесс; бот один. Второй инстанс — переезжать на
# блокировку в БД.
BUSY: set[int] = set()

# Копия админам и печатный разворот крутятся фоном. Ссылки держим сами:
# asyncio хранит задачи слабой ссылкой, и без этого сборщик может убить их
# на середине запроса.
_TASKS: set[asyncio.Task] = set()


def t(lang: str, key: str, **kw) -> str:
    value = I18N.get(lang, I18N["ru"]).get(key) or I18N["ru"][key]
    return value.format(**kw) if kw else value


def session(user_id: int) -> quiz.Session:
    if user_id not in SESSIONS:
        SESSIONS[user_id] = quiz.Session()
    return SESSIONS[user_id]


def _kb(rows: list[list[tuple[str, str]]]) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
        for row in rows
    ])


async def _ask_language(message: types.Message) -> None:
    """Первый экран. Он же ответ на любое сообщение от гостя без сессии:
    на стенде люди пишут «привет» раньше, чем жмут /start, и молчание в ответ
    читается как «бот сдох»."""
    await message.answer(
        "Тілді таңдаңыз / Выбери язык / Choose a language",
        reply_markup=_kb([[(I18N[code]["lang_name"], f"lang:{code}")] for code in I18N]),
    )


@router.message(CommandStart())
@router.message(Command("restart"))
async def on_start(message: types.Message) -> None:
    SESSIONS.pop(message.from_user.id, None)
    await _ask_language(message)


@router.callback_query(F.data.startswith("lang:"))
async def on_lang(call: types.CallbackQuery, db) -> None:
    lang = call.data.split(":", 1)[1]
    state = session(call.from_user.id)
    state.lang = lang if lang in I18N else config.DEFAULT_LANG

    if await storage.issued_count(db, call.from_user.id) >= config.PASSPORT_LIMIT:
        await call.message.edit_text(t(state.lang, "limit_reached"))
        await call.answer()
        return

    await call.message.edit_text(
        t(state.lang, "start"), parse_mode="HTML",
        reply_markup=_kb([[(t(state.lang, "begin"), "quiz:go")]]),
    )
    await call.answer()


@router.callback_query(F.data == "quiz:go")
async def on_begin(call: types.CallbackQuery) -> None:
    """Сначала имя: тюркизация идёт в Gemini, и пока гость отвечает на четыре
    вопроса, ответ уже готов — на стенде это снимает паузу из живой очереди."""
    state = session(call.from_user.id)
    state.step, state.scores, state.name, state.started = 0, {}, None, True
    state.foreign = None
    await call.message.edit_text(t(state.lang, "ask_name"), parse_mode="HTML")
    await call.answer()


def _next_prompt(state: quiz.Session) -> tuple[str, types.InlineKeyboardMarkup | None]:
    """Что показать гостю, закончившему квиз: гражданство или просьбу о фото.

    Одно место на все входы — ответ на кнопку, напоминание в ответ на текст и
    фото, присланное раньше времени. Иначе экраны разъезжаются.
    """
    if state.foreign is None:
        return (t(state.lang, "ask_citizenship"),
                _kb([[(t(state.lang, "citizen_kz"), "cit:kz")],
                     [(t(state.lang, "citizen_other"), "cit:other")]]))
    return t(state.lang, "ask_photo", name=state.name), None


def _question(state: quiz.Session) -> tuple[str, types.InlineKeyboardMarkup]:
    text, options = quiz.question(state.step, state.lang)
    header = f"<b>{state.step + 1}/{len(quiz.QUESTIONS)}</b>  {text}"
    return header, _kb([[(opt, f"ans:{state.step}:{i}")] for i, opt in enumerate(options)])


@router.callback_query(F.data.startswith("ans:"))
async def on_answer(call: types.CallbackQuery) -> None:
    _, step, index = call.data.split(":")
    state = session(call.from_user.id)
    if int(step) != state.step or not state.name:   # двойной тап или старая клавиатура
        await call.answer()
        return

    state.answer(int(index))
    if state.finished:
        # Гражданство спрашиваем как ещё одно поле документа — так экран не
        # выглядит анкетой не по делу. От ответа зависит только печатный
        # разворот; гость в любом случае получит свои 16:9 и ничего больше.
        text, kb = _next_prompt(state)
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        header, kb = _question(state)
        await call.message.edit_text(header, parse_mode="HTML", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("cit:"))
async def on_citizenship(call: types.CallbackQuery) -> None:
    state = session(call.from_user.id)
    if not state.finished or not state.name:        # тап по старой клавиатуре
        await call.answer()
        return
    state.foreign = call.data.split(":", 1)[1] == "other"
    await call.message.edit_text(
        t(state.lang, "ask_photo", name=state.name), parse_mode="HTML")
    await call.answer()


@router.message(F.text & ~F.text.startswith("/"))
async def on_name(message: types.Message, db) -> None:
    state = session(message.from_user.id)
    if not state.started:
        await _ask_language(message)
        return
    if state.name:
        if state.finished:                # ждём кнопку или фото, а прислали текст
            text, kb = _next_prompt(state)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
        return

    raw = message.text.strip()
    if len(raw) > 30:
        await message.answer(t(state.lang, "name_too_long"))
        return
    if not any(ch.isalpha() for ch in raw):
        await message.answer(t(state.lang, "name_bad"))
        return

    latin, meaning, _ = await names.turkify(db, raw)
    state.name = latin
    # meaning пришло от модели, а сообщение уходит с parse_mode=HTML: одна
    # угловая скобка в ответе — и Telegram не распарсит, гость не увидит ничего.
    suffix = t(state.lang, "meaning_suffix", meaning=html.escape(meaning)) if meaning else ""
    await message.answer(
        t(state.lang, "name_set", name=latin, meaning=suffix), parse_mode="HTML")

    header, kb = _question(state)
    await message.answer(header, parse_mode="HTML", reply_markup=kb)


@router.message(F.document | F.video | F.animation)
async def on_not_photo(message: types.Message) -> None:
    """Фото, присланное файлом, в F.photo не попадает — без этого бот молчит."""
    state = session(message.from_user.id)
    if not state.started:
        await _ask_language(message)
    elif state.finished and state.name:
        await message.answer(t(state.lang, "send_as_photo"))


@router.message(F.photo)
async def on_photo(message: types.Message, db, bot) -> None:
    user_id = message.from_user.id
    state = session(user_id)
    # Лимит проверяем ПЕРВЫМ: у того, кто уже получил паспорт, сессия сброшена,
    # и по второму условию он получил бы «сломалось» вместо «паспорт уже есть».
    if await storage.issued_count(db, user_id) >= config.PASSPORT_LIMIT:
        await message.answer(t(state.lang, "limit_reached"))
        return

    if not state.started:                 # фото прилетело раньше, чем гость начал
        await _ask_language(message)
        return
    if not state.finished or not state.name:
        await message.answer(t(state.lang, "error"))
        return
    if state.foreign is None:             # квиз прошёл, а гражданство не выбрал
        text, kb = _next_prompt(state)
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
        return

    if user_id in BUSY:                   # второе фото, пока первое рендерится
        return
    BUSY.add(user_id)
    note = await message.answer(t(state.lang, "working"))
    try:
        await _issue(message, note, db, bot, state, user_id)
    finally:
        BUSY.discard(user_id)


async def _issue(message: types.Message, note: types.Message, db, bot,
                 state: quiz.Session, user_id: int) -> None:
    raw = io.BytesIO()
    await bot.download(message.photo[-1], destination=raw)
    data = raw.getvalue()

    try:
        face = await asyncio.to_thread(photo.find_face, data)
    except photo.NoFace:
        await note.edit_text(t(state.lang, "no_face"))
        return                            # попытка НЕ засчитана

    totem_id = quiz.resolve(state.scores, user_id)
    cfg = TOTEMS[totem_id]

    # Два стоп-крана: ручной (/ai off) и дневной потолок. Оба не останавливают
    # выдачу — доделываем локальной сепией, но паспорт гость получает.
    budget_left = (await storage.get_flag(db, "ai_enabled", "on") == "on"
                   and await storage.ai_used_today(db) < config.DAILY_AI_BUDGET)
    try:
        portrait = await asyncio.to_thread(photo.crop_to_portrait, data, face)
        if budget_left:
            await note.edit_text(t(state.lang, "working_ai"))
        styled, used_ai = await photo.stylize(portrait, cfg["stylize"]) if budget_left \
            else (await asyncio.to_thread(photo.sepia, portrait), False)

        await note.edit_text(t(state.lang, "working_render"))
        serial, code = await storage.next_serial(db)
        buf = io.BytesIO()
        styled.save(buf, "PNG")
        buf.seek(0)

        card = await asyncio.to_thread(
            compose.render_from_bytes,
            compose.Passport(name=state.name, serial=serial, code=code, totem_id=totem_id),
            buf.getvalue(),
        )
    except Exception:                     # noqa: BLE001 — гостю нужен ответ, не трейс
        log.exception("не удалось собрать паспорт для %s", user_id)
        await note.edit_text(t(state.lang, "error"))
        return

    out = io.BytesIO()
    card.save(out, "PNG", optimize=True)
    out.seek(0)

    totem_name = I18N.get(state.lang, I18N["ru"])["totems"][totem_id]
    caption_key = "done" if used_ai else "done_fallback"
    motto = " ".join(cfg["passport"]["motto"])
    await note.delete()
    await message.answer_photo(
        types.BufferedInputFile(out.getvalue(), filename=f"steppe_{serial}.png"),
        caption=t(state.lang, caption_key, totem=totem_name, motto=motto),
        parse_mode="HTML",
    )

    await storage.record(db, user_id, serial, totem_id, state.name, used_ai)

    # Дальше — фоном. Гость своё уже получил: что бы тут ни упало, на его
    # выдачу это не влияет.
    if config.ADMIN_COPY and config.ADMIN_IDS:
        _spawn(_admin_copy(bot, out.getvalue(), state.name, serial, totem_id, user_id))
    if state.foreign and spread.enabled() and budget_left:
        _spawn(_print_branch(bot, out.getvalue(), state.name, serial, totem_id, cfg))

    SESSIONS.pop(user_id, None)           # фото и состояние больше не нужны


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _admin_copy(bot, card: bytes, name: str, serial: str,
                      totem_id: str, user_id: int) -> None:
    """Тот же паспорт — каждому админу в личку.

    Документом, а не фото: у гостя копия и так пережата телеграмом, а у
    оператора должен остаться исходный PNG — по нему перепечатывают.

    Каждый админ отправляется отдельно и в своём try: один не нажал /start у
    бота — остальные всё равно получат.
    """
    caption = (f"📄 <b>{name}</b> · {I18N['ru']['totems'][totem_id]}\n"
               f"Серия: <code>{serial}</code>\n"
               f"Гость: <code>{user_id}</code>")
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_document(
                admin_id,
                types.BufferedInputFile(card, filename=f"steppe_{serial}.png"),
                caption=caption, parse_mode="HTML",
            )
        except Exception:                 # noqa: BLE001 — админ мог не начать диалог
            log.warning("копия паспорта %s не ушла админу %s", serial, admin_id,
                        exc_info=True)


def _sheet_png(img, size_mm) -> bytes:
    return print_sheet.to_png(print_sheet.sheet(img, size_mm))


async def _print_branch(bot, card: bytes, name: str, serial: str,
                        totem_id: str, cfg: dict) -> None:
    """Шаги 2–4: разворот через API, печатный лист, отправка оператору.

    Документом, а не фото: sendPhoto пережимает файл в JPEG и уносит с собой
    и 300 DPI, и половину деталей карандашного штриха.
    """
    p = cfg["passport"]
    try:
        img = await spread.render(card, name, serial, p["territory"], " ".join(p["motto"]))
    except Exception:                     # noqa: BLE001 — печать не должна ронять бота
        log.exception("разворот %s не собрался", serial)
        return

    sizes = [(print_sheet.MAIN_MM, False)]
    if config.PRINT_BLEED:
        sizes.append((print_sheet.BLEED_MM, True))

    totem_name = I18N["ru"]["totems"][totem_id]
    for size_mm, bleed in sizes:
        try:
            data = await asyncio.to_thread(_sheet_png, img, size_mm)
            caption = (
                f"🖨 <b>{name}</b> · {totem_name}\n"
                f"Серия: <code>{serial}</code>\n"
                f"{size_mm[0]:g}×{size_mm[1]:g} мм"
                f"{f' + вылет {print_sheet.BLEED_PER_SIDE_MM:g} мм' if bleed else ''}"
                f" · {print_sheet.DPI} DPI"
            )
            await bot.send_document(
                config.PRINT_CHAT_ID,
                types.BufferedInputFile(
                    data, filename=print_sheet.filename(serial, size_mm, bleed)),
                caption=caption,
                parse_mode="HTML",
            )
        except Exception:                 # noqa: BLE001
            log.exception("не отправился печатный лист %s (%s мм)", serial, size_mm[0])
