#!/usr/bin/env python3
"""Точка входа. Long-polling: домен и вебхуки для стенда не нужны.

    python3 -m bot.main
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from . import config, storage
from .handlers import admin, flow


class Deps(BaseMiddleware):
    """Прокидывает соединение с БД в хендлеры — без глобалов."""

    def __init__(self, db) -> None:
        self.db = db

    async def __call__(self, handler, event, data):
        data["db"] = self.db
        return await handler(event, data)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    gaps = config.missing()
    if gaps:
        print("Не хватает настроек в .env:\n" + "\n".join(f"  • {g}" for g in gaps))
        sys.exit(1)
    if not config.ADMIN_IDS:
        logging.warning("ADMIN_IDS пуст — админ-команды недоступны никому. "
                        "Узнать свой id: команда /whoami")

    db = await storage.connect()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=None))
    dp = Dispatcher()
    dp.message.middleware(Deps(db))
    dp.callback_query.middleware(Deps(db))
    dp.include_router(admin.router)
    dp.include_router(flow.router)

    me = await bot.get_me()
    logging.info("@%s поднят. Стилизация: %s, лимит: %s паспорт(ов) на гостя",
                 me.username, "вкл" if config.AI_STYLIZE else "выкл", config.PASSPORT_LIMIT)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
