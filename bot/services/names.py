"""Тюркизация имени: Johny → ZHANIBEK.

На паспорт идёт латиница заглавными — так набрано поле NAME в макете.
Результат кешируется в SQLite: на комикконе имена повторяются массово.

Провайдер — любой с OpenAI-совместимым /v1/chat/completions (llm.alem.ai, сам
OpenAI, локальный сервер). Адрес, ключ и модель берутся из .env.
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

# Очередь к провайдеру. Минутный лимит считается по запросам в минуту, а на
# стенде имена уходят пачками: пять человек в очереди — пять параллельных
# вызовов, и 429 прилетает на ровном месте. Гейт растягивает пачку во времени.
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
    """Сколько ждать по мнению самого провайдера. Заголовок или тело ответа."""
    header = resp.headers.get("retry-after")
    if header and header.strip().isdigit():
        return float(header.strip())
    try:
        for detail in resp.json()["error"].get("details", []):
            delay = str(detail.get("retryDelay", ""))
            if delay.endswith("s"):
                return float(delay[:-1])
    except (AttributeError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


def _extract_json(text: str) -> dict | None:
    """Достаёт объект из ответа модели.

    Не все OpenAI-совместимые серверы уважают response_format, и модель может
    обернуть JSON в ```-блок или подпереть его фразой. Берём первый объект от
    «{» до последней «}» — этого хватает, ответ у нас из двух полей.
    """
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _ask(source: str) -> dict | None:
    """Запрос к провайдеру с ретраями. None — не сложилось, зовущий берёт запасное.

    Ждать долго нельзя: гость стоит в живой очереди и смотрит на «печатаю».
    Поэтому попыток мало и пауза короткая — вытащить минутный лимит, а не
    пересидеть исчерпанную квоту.
    """
    payload = {
        "model": config.NAME_MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": source}],
        "temperature": 0.85,
        # Сервер может не знать этого поля — тогда просто игнорирует, а JSON
        # мы всё равно вытащим из текста.
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {config.NAME_API_KEY}",
               "Content-Type": "application/json"}

    async with _GATE:
        for attempt in range(config.NAME_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=45) as client:
                    resp = await client.post(config.NAME_API_URL,
                                             json=payload, headers=headers)
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    parsed = _extract_json(content)
                    if parsed is None:
                        log.warning("имена: ответ не разобрать как JSON: %.200s", content)
                    return parsed

                if resp.status_code in (408, 429, 500, 502, 503, 504):
                    if attempt == config.NAME_RETRIES:
                        log.warning("имена: %s, попытки кончились — запасное имя. "
                                    "Если это 429 и он держится, кончилась квота "
                                    "ключа NAME_API_KEY", resp.status_code)
                        return None
                    pause = min(_retry_after(resp) or 1.5 * (attempt + 1), 6.0)
                    log.info("имена: %s, жду %.1f с и повторяю", resp.status_code, pause)
                    await asyncio.sleep(pause)              # гость ждёт, не тянем
                    continue

                # 401/403 — неверный ключ, 404 — не тот URL, 400 — не та модель.
                log.warning("имена: провайдер ответил %s — запасное имя. %.200s",
                            resp.status_code, resp.text)
                return None
            except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
                if attempt == config.NAME_RETRIES:
                    log.warning("имена: провайдер недоступен (%s) — запасное имя", exc)
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

    if not config.NAME_API_KEY or not config.NAME_MODEL:
        return fallback_for(source), "", False

    parsed = await _ask(source)
    if parsed is None:
        return fallback_for(source), "", False
    latin = _sanitize(parsed.get("latin", ""))
    if not latin:
        return fallback_for(source), "", False

    meaning = str(parsed.get("meaning", ""))[:60]
    await storage.cache_name(db, source, latin, meaning)
    return latin, meaning, True
