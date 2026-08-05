"""Команды оператора. Работают с телефона — на стенде это единственный терминал."""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router, types
from aiogram.filters import Command

from .. import config, storage
from ..render import compose, print_sheet

log = logging.getLogger(__name__)
router = Router()


def _is_admin(message: types.Message) -> bool:
    return message.from_user.id in config.ADMIN_IDS


@router.message(Command("stats"))
async def on_stats(message: types.Message, db) -> None:
    if not _is_admin(message):
        return
    s = await storage.stats(db)
    by_totem = "\n".join(f"  {k}: {v}" for k, v in s["by_totem"].items()) or "  пока пусто"
    await message.answer(
        f"<b>Выдано всего:</b> {s['total']}\n"
        f"<b>Сегодня:</b> {s['today']}  (с AI: {s['ai_today']} из {config.DAILY_AI_BUDGET})\n"
        f"<b>Имён в кеше:</b> {s['names_cached']}\n\n"
        f"<b>По тотемам</b>\n{by_totem}",
        parse_mode="HTML",
    )


@router.message(Command("reset"))
async def on_reset(message: types.Message, db) -> None:
    """Вернуть гостю попытку. Нужно, когда на стенде что-то пошло не так."""
    if not _is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Формат: /reset &lt;user_id&gt;", parse_mode="HTML")
        return
    removed = await storage.reset_user(db, int(parts[1]))
    await message.answer(f"Снято записей: {removed}. Гость может пройти заново.")


@router.message(Command("ai"))
async def on_ai(message: types.Message, db) -> None:
    """Ручной выключатель стилизации: если провайдер лёг, очередь не должна встать."""
    if not _is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or parts[1] not in {"on", "off"}:
        current = await storage.get_flag(db, "ai_enabled", "on")
        await message.answer(f"Стилизация сейчас: <b>{current}</b>\nФормат: /ai on | /ai off",
                             parse_mode="HTML")
        return
    await storage.set_flag(db, "ai_enabled", parts[1])
    await message.answer(f"Стилизация: <b>{parts[1]}</b>", parse_mode="HTML")


# Админ — в ФИЛЬТРЕ, а не в теле: сработавший хендлер обрывает цепочку, и
# пересылка от гостя пропала бы молча, не дойдя до сценария.
@router.message(F.forward_origin, F.from_user.id.in_(config.ADMIN_IDS))
async def on_forward(message: types.Message) -> None:
    """Определитель chat_id: перешли сюда пост из канала — верну его номер.

    Иначе id пришлось бы узнавать через сторонние id-боты, то есть пересылать
    им содержимое своего закрытого канала.
    """
    origin = message.forward_origin
    chat = getattr(origin, "chat", None)
    if chat is not None:                  # канал или группа
        await message.answer(
            f"<b>{chat.title or 'без названия'}</b>\n"
            f"id: <code>{chat.id}</code>\n\n"
            "Это и есть значение для <code>PRINT_USER_CHAT_ID</code> в .env.\n"
            "После правки: <code>sudo systemctl restart comicon-bot</code>, "
            "потом <code>/printtest</code>.",
            parse_mode="HTML")
        return

    user = getattr(origin, "sender_user", None)
    if user is not None:
        await message.answer(f"Это пересылка от пользователя, id: "
                             f"<code>{user.id}</code>. Для печати нужен id канала.",
                             parse_mode="HTML")
        return

    await message.answer(
        "Отправитель скрыт настройками приватности — id из такой пересылки не "
        "виден. Перешли пост именно из канала печати.")


@router.message(Command("printtest"))
async def on_printtest(message: types.Message, bot) -> None:
    """Проверка канала печати: доходит ли туда файл и хватает ли боту прав.

    Картинку НЕ генерируем — собираем демо-паспорт локально и гоним через ту же
    постобработку. Проверяется ровно проводка: id канала, права бота, отправка
    документом. Стоит ноль, поэтому дёргать можно сколько угодно.
    """
    if not _is_admin(message):
        return

    from .flow import _print_targets, _send_document                  # noqa: PLC0415

    targets = _print_targets()
    if not targets:
        await message.answer("Некому слать: ADMIN_IDS пуст и PRINT_USER_CHAT_ID не задан.")
        return

    try:
        card = await asyncio.to_thread(
            compose.render,
            compose.Passport(name="TESTBEK", serial="000000000",
                             code="0000", totem_id="at"),
            None,
        )
        data = await asyncio.to_thread(
            lambda: print_sheet.to_png(print_sheet.sheet(card, print_sheet.MAIN_MM)))
    except Exception as exc:              # noqa: BLE001 — оператору нужна причина
        log.exception("printtest не прошёл")
        await message.answer(f"Не собралось: <code>{type(exc).__name__}: {exc}</code>",
                             parse_mode="HTML")
        return

    await _send_document(
        bot, targets, data, "printtest_125x176mm_300dpi.png",
        "Проверка доставки. Это не заказ — так будут приходить готовые файлы "
        "зарубежных гостей.", "тестовый лист")
    await message.answer(
        "Отправлено: <code>" + "</code>, <code>".join(str(x) for x in targets)
        + "</code>\n\nНе дошло кому-то из списка — он не нажимал /start у бота "
          "(для лички) или бот не админ в канале (для канала).",
        parse_mode="HTML")


@router.message(Command("whoami"))
async def on_whoami(message: types.Message) -> None:
    role = "админ" if _is_admin(message) else "гость"
    await message.answer(f"user_id: <code>{message.from_user.id}</code> — {role}",
                         parse_mode="HTML")
