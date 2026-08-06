"""Тюркизация имени через Gemini: Johny → ZHANIBEK.

На паспорт идёт латиница заглавными — так набрано поле NAME в макете.
Результат кешируется в SQLite: на комикконе имена повторяются массово.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata

import httpx

from .. import config, storage

log = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Очередь на Gemini. Минутный лимит считается по запросам в минуту, а на стенде
# имена уходят пачками: пять человек в очереди — пять параллельных вызовов, и
# 429 прилетает на ровном месте. Гейт растягивает пачку во времени.
_GATE = asyncio.Semaphore(config.NAME_CONCURRENCY)

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


def _retry_after(resp: httpx.Response) -> float | None:
    """Сколько ждать по мнению самого Gemini. Заголовок или RetryInfo в теле."""
    header = resp.headers.get("retry-after")
    if header and header.isdigit():
        return float(header)
    try:
        for detail in resp.json()["error"].get("details", []):
            delay = detail.get("retryDelay", "")
            if delay.endswith("s"):
                return float(delay[:-1])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


async def _ask(payload: dict) -> dict | None:
    """Запрос к Gemini с ретраями. None — не сложилось, зовущий берёт запасное.

    Ждать долго нельзя: гость стоит в живой очереди и смотрит на «печатаю».
    Поэтому попыток мало и пауза короткая — вытащить минутный лимит, а не
    пересидеть суточный.
    """
    async with _GATE:
        for attempt in range(config.NAME_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=45) as client:
                    resp = await client.post(
                        API.format(model=config.TEXT_MODEL),
                        json=payload,
                        headers={"x-goog-api-key": config.GEMINI_API_KEY},
                    )
                if resp.status_code == 200:
                    return json.loads(
                        resp.json()["candidates"][0]["content"]["parts"][0]["text"])

                if resp.status_code in (429, 500, 503):
                    if attempt == config.NAME_RETRIES:
                        log.warning("Gemini %s, попытки кончились — запасное имя. "
                                    "Если это 429 и он не проходит, кончилась "
                                    "суточная квота ключа", resp.status_code)
                        return None
                    pause = _retry_after(resp) or 1.5 * (attempt + 1)
                    log.info("Gemini %s, жду %.1f с и повторяю",
                             resp.status_code, min(pause, 6.0))
                    await asyncio.sleep(min(pause, 6.0))    # гость ждёт, не тянем
                    continue

                log.warning("Gemini ответил %s — запасное имя", resp.status_code)
                return None
            except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
                if attempt == config.NAME_RETRIES:
                    log.warning("Gemini недоступен (%s) — запасное имя", exc)
                    return None
                await asyncio.sleep(1.5 * (attempt + 1))
    return None


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
    parsed = await _ask(payload)
    if parsed is None:
        return fallback_for(source), "", False
    latin = _sanitize(parsed.get("latin", ""))
    if not latin:
        return fallback_for(source), "", False

    meaning = str(parsed.get("meaning", ""))[:60]
    await storage.cache_name(db, source, latin, meaning)
    return latin, meaning, True
