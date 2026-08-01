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


def missing() -> list[str]:
    """Чего не хватает для старта. Проверяем до подключения к Telegram."""
    gaps = []
    if not BOT_TOKEN:
        gaps.append("BOT_TOKEN — возьми у @BotFather")
    if AI_STYLIZE and not OPENAI_API_KEY:
        gaps.append("OPENAI_API_KEY — или выключи AI_STYLIZE_ENABLED")
    if not GEMINI_API_KEY:
        gaps.append("GEMINI_API_KEY — без него имена не тюркизируются")
    return gaps
