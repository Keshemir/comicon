"""Команды оператора. Работают с телефона — на стенде это единственный терминал."""

from __future__ import annotations

from aiogram import Router, types
from aiogram.filters import Command

from .. import config, storage

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


@router.message(Command("whoami"))
async def on_whoami(message: types.Message) -> None:
    role = "админ" if _is_admin(message) else "гость"
    await message.answer(f"user_id: <code>{message.from_user.id}</code> — {role}",
                         parse_mode="HTML")
