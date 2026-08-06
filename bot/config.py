"""Настройки из .env. Всё с дефолтами — бот должен подниматься без правки кода."""

from __future__ import annotations

import os
import pathlib

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _ints(name: str) -> set[int]:
    raw = os.getenv(name, "")
    return {int(x) for x in raw.replace(" ", "").split(",") if x.lstrip("-").isdigit()}


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = _ints("ADMIN_IDS")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Модели пиним на версии: алиас *-latest молча переедет и утащит цену и качество.
IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.6-flash")

# --- тюркизация имён ------------------------------------------------------
# Эндпоинт OpenAI-совместимый (/v1/chat/completions): под этот формат ходят и
# llm.alem.ai, и сам OpenAI, и почти все локальные серверы. Сменить провайдера
# = поменять три переменные в .env, код трогать не надо.
NAME_API_URL = os.getenv("NAME_API_URL", "https://llm.alem.ai/v1/chat/completions")
NAME_API_KEY = os.getenv("NAME_API_KEY", "").strip()
NAME_MODEL = os.getenv("NAME_MODEL", "").strip()

# 429 бывает минутный (проходит за секунды) и квотный (до продления). Ретраи
# вытаскивают первый; на втором бот уходит на запасной список имён.
NAME_RETRIES = int(os.getenv("NAME_MAX_RETRIES", "2"))
NAME_CONCURRENCY = int(os.getenv("NAME_CONCURRENCY", "4"))

PASSPORT_LIMIT = int(os.getenv("PASSPORT_LIMIT_PER_USER", "1"))
AI_STYLIZE = _flag("AI_STYLIZE_ENABLED", True)
AI_RETRIES = int(os.getenv("AI_MAX_RETRIES", "3"))
NAME_CACHE = _flag("NAME_CACHE_ENABLED", True)

# Стоп-кран на день: уперлись — бот сам уходит в режим без AI, но продолжает
# выдавать паспорта. На стенде это лучше, чем остановиться посреди очереди.
DAILY_AI_BUDGET = int(os.getenv("DAILY_AI_BUDGET", "1200"))

# Сколько стилизаций крутится одновременно. Упрёмся не в железо, а в rate limit
# провайдера; при 429 очередь просто подождёт.
CONCURRENCY = int(os.getenv("STYLIZE_CONCURRENCY", "4"))

DB_PATH = ROOT / os.getenv("DB_PATH", "data/comicon.db")
CONTENT = ROOT / "content"
DEFAULT_LANG = os.getenv("DEFAULT_LANG", "ru")

# Копия каждого выданного паспорта в личку админам. Пустой ADMIN_IDS сам по
# себе выключает: слать некому.
ADMIN_COPY = _flag("ADMIN_COPY_ENABLED", True)

# --- печатный разворот ---------------------------------------------------
# Второй, невидимый гостю шаг: из готовой 16:9 картинки собирается вертикальный
# разворот под физический паспорт-холдер и уходит оператору печати.
#
# Пустой PRINT_USER_CHAT_ID = ветка выключена. Это и есть выключатель: без
# адресата отправлять некуда, и гонять API впустую незачем.
PRINT_CHAT_ID = int(os.getenv("PRINT_USER_CHAT_ID", "0") or "0")
PRINT_ENABLED = _flag("PRINT_ENABLED", True)

# Портретный размер у edit-эндпоинта.
#
# gpt-image-2 принимает ЛЮБОЙ размер, а не три пресета: обе стороны кратны 16,
# длинная ≤ 3840, отношение не круче 3:1, пикселей от 655 360 до 8 294 400.
# 1744×2336 — это 3:4, на котором ТЗ проверяло промпт, и оно во все лимиты
# укладывается. Постобработка всё равно считает кроп от фактических W×H, так
# что размер можно менять, не трогая код.
PRINT_SIZE = os.getenv("PRINT_IMAGE_SIZE", "1744x2336")

# Своя очередь: печать идёт фоном и не должна отъедать rate limit у живой
# стилизации, ради которой гость стоит у стенда.
PRINT_CONCURRENCY = int(os.getenv("PRINT_CONCURRENCY", "2"))
PRINT_RETRIES = int(os.getenv("PRINT_MAX_RETRIES", "3"))

# Проверка на «животное осталось в документе» и один повтор при браке.
PRINT_QC = _flag("PRINT_QC_ENABLED", True)

# Версия с вылетами под типографию — вторым документом, по запросу.
PRINT_BLEED = _flag("PRINT_BLEED", False)


def missing() -> list[str]:
    """Чего не хватает для старта. Проверяем до подключения к Telegram."""
    gaps = []
    if not BOT_TOKEN:
        gaps.append("BOT_TOKEN — возьми у @BotFather")
    if AI_STYLIZE and not OPENAI_API_KEY:
        gaps.append("OPENAI_API_KEY — или выключи AI_STYLIZE_ENABLED")
    if not NAME_API_KEY:
        gaps.append("NAME_API_KEY — без него имена не тюркизируются")
    if NAME_API_KEY and not NAME_MODEL:
        gaps.append("NAME_MODEL — спроси у провайдера: curl $NAME_API_URL/../models")
    return gaps
