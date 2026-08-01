"""Тюркизация имени через Gemini: Johny → ZHANIBEK.

На паспорт идёт латиница заглавными — так набрано поле NAME в макете.
Результат кешируется в SQLite: на комикконе имена повторяются массово.
"""

from __future__ import annotations

import json
import re
import unicodedata

import httpx

from .. import config, storage

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM = """Ты нарекаешь гостя вымышленным тюркским именем для сувенирного «паспорта Степи».

Правила:
1. Подбери НАСТОЯЩЕЕ казахское или тюркское имя, созвучное исходному. Первый звук и ритм важнее буквального перевода.
2. Нетюркское имя заменять ОБЯЗАТЕЛЬНО. Никогда не возвращай исходное имя как есть.
3. Имя уже тюркское — оставь его, только приведи к нормальной орфографии.
4. Сохрани род, если он ясен из исходного имени. Неясен — выбирай мужское.
5. Верни СТРОГО JSON: {"latin":"<ЗАГЛАВНЫМИ, только A-Z>","meaning":"<значение по-русски, 2-4 слова>"}

В latin допустимы только буквы A-Z без диакритики: имя печатается на бланке
шрифтом, где нет казахских букв. Жәнібек → ZHANIBEK, Құдайберген → QUDAIBERGEN."""

# Запасной набор на случай, когда Gemini недоступен. Лучше выдать паспорт с
# приблизительным именем, чем не выдать вовсе — гость стоит в живой очереди.
FALLBACK = [
    "ALASH", "ARMAN", "BATYR", "BEKZAT", "DAULET", "ERBOL", "JIGER", "KAIRAT",
    "MURAT", "NURLAN", "SANZHAR", "TEMIR", "ZHANIBEK", "ZHASULAN",
]
FALLBACK_F = ["AIGUL", "AIZHAN", "ALTYN", "AKERKE", "DANA", "GULNAR",
              "KAMSHAT", "MADINA", "SAULE", "ZHANNA"]


def _sanitize(value: str) -> str:
    """Оставляет только A-Z: на бланке машинописный шрифт без диакритики."""
    folded = unicodedata.normalize("NFKD", value)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z]", "", folded).upper()[:18]


def fallback_for(source: str) -> str:
    """Детерминированно: один и тот же ввод всегда даёт одно и то же имя."""
    import zlib

    pool = FALLBACK_F if source.strip().lower().endswith(("a", "я", "ь")) else FALLBACK
    return pool[zlib.crc32(source.strip().lower().encode()) % len(pool)]


async def turkify(db, source: str) -> tuple[str, str, bool]:
    """Возвращает (латиница, значение, дошли ли до модели)."""
    source = (source or "").strip()
    if not source:
        return fallback_for("—"), "", False

    hit = await storage.cached_name(db, source)
    if hit:
        return hit[0], hit[1], True

    if not config.GEMINI_API_KEY:
        return fallback_for(source), "", False

    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"parts": [{"text": source}]}],
        "generationConfig": {"temperature": 0.85, "responseMimeType": "application/json"},
    }
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                API.format(model=config.TEXT_MODEL),
                json=payload,
                headers={"x-goog-api-key": config.GEMINI_API_KEY},
            )
        if resp.status_code != 200:
            return fallback_for(source), "", False
        parsed = json.loads(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
        latin = _sanitize(parsed.get("latin", ""))
        if not latin:
            return fallback_for(source), "", False
    except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError):
        return fallback_for(source), "", False

    meaning = str(parsed.get("meaning", ""))[:60]
    await storage.cache_name(db, source, latin, meaning)
    return latin, meaning, True
