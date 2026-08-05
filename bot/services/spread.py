"""Шаг 2: из готового паспорта 16:9 — вертикальный разворот под печать.

Гость этого не видит. После того как ему ушла его картинка, бот делает ещё
один edit-вызов: на вход идёт та же самая 16:9, на выходе — портретный
разворот, где сверху документ без животного, а снизу это же животное крупно.

Промпт один на все четыре тотема и НЕ называет животное — модель берёт его из
входной картинки. Меняются только плейсхолдеры.

Известный глюк edit-моделей: животное остаётся и в документе тоже, получается
два. Промпт это запрещает последней строкой, но не всегда — поэтому есть
проверка зрением и один повтор.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re

import httpx
import yaml
from PIL import Image

from .. import config

log = logging.getLogger(__name__)

EDIT = "https://api.openai.com/v1/images/edits"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_PROMPTS = yaml.safe_load((config.CONTENT / "prompts.yaml").read_text(encoding="utf-8"))
_GATE = asyncio.Semaphore(config.PRINT_CONCURRENCY)


class SpreadFailed(Exception):
    """Разворот не собрался. Гостя это не касается — он своё уже получил."""


def enabled() -> bool:
    """Есть чем генерировать и есть кому слать.

    Адресат по умолчанию — админы; PRINT_USER_CHAT_ID нужен, только если
    печать складывают в отдельный канал.
    """
    return bool(config.PRINT_ENABLED and config.OPENAI_API_KEY
                and (config.ADMIN_IDS or config.PRINT_CHAT_ID))


def mrz(value: str) -> str:
    """Строка в MRZ-формате: всё, что не A-Z и не 0-9, становится «<».

    BETPAQ-DALA → BETPAQ<DALA, как в образце из ТЗ.
    """
    return re.sub(r"[^A-Z0-9]+", "<", value.upper()).strip("<")


def build_prompt(name: str, serial: str, territory: str, motto: str) -> str:
    fields = {
        "{NAME}": mrz(name),
        "{SERIAL}": serial,
        "{TERRITORY_MRZ}": mrz(territory),
        "{MOTTO}": motto,
    }
    prompt = _PROMPTS["spread_base"]
    for key, value in fields.items():
        prompt = prompt.replace(key, value)
    return prompt


async def _edit(card: bytes, prompt: str) -> Image.Image:
    """Один проход edit-эндпоинта. Ретраи только на сетевые и 5xx."""
    for attempt in range(config.PRINT_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(
                    EDIT,
                    headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                    files={"image": ("passport.png", card, "image/png")},
                    data={"model": config.IMAGE_MODEL, "prompt": prompt,
                          "size": config.PRINT_SIZE, "n": "1"},
                )
            if resp.status_code == 200:
                body = resp.json()
                # Цена у провайдера потокенная, а не за картинку, и зависит от
                # запрошенного размера. Пишем usage в журнал: после первого
                # десятка гостей счёт считается по факту, а не на глаз.
                usage = body.get("usage") or {}
                if usage:
                    log.info("разворот: токены вход %s / выход %s (%s)",
                             usage.get("input_tokens"), usage.get("output_tokens"),
                             config.PRINT_SIZE)
                payload = body["data"][0]["b64_json"]
                return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")
            if resp.status_code in (400, 403):
                raise SpreadFailed(f"отказ провайдера {resp.status_code}: {resp.text[:200]}")
            log.warning("разворот: HTTP %s, попытка %s", resp.status_code, attempt + 1)
        except httpx.HTTPError as exc:
            log.warning("разворот: сеть легла (%s), попытка %s", exc, attempt + 1)
        await asyncio.sleep(2.0 * (attempt + 1))
    raise SpreadFailed("провайдер не ответил за все попытки")


async def _looks_clean(img: Image.Image) -> bool:
    """Животное ровно одно и в документе его нет?

    Считает текстовая модель — она мультимодальная, и это один дешёвый вызов.
    Если проверка сама не сработала (нет ключа, лёг API, пришёл не JSON),
    считаем кадр годным: заворачивать тираж из-за упавшего судьи неправильно,
    оператор всё равно смотрит файл глазами перед печатью.
    """
    if not config.PRINT_QC or not config.GEMINI_API_KEY:
        return True

    thumb = img.copy()
    thumb.thumbnail((768, 768), Image.LANCZOS)      # судье хватает, токены дешевле
    buf = io.BytesIO()
    thumb.save(buf, "JPEG", quality=82)

    payload = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": "image/jpeg",
                             "data": base64.b64encode(buf.getvalue()).decode()}},
            {"text": _PROMPTS["spread_check"]},
        ]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                GEMINI.format(model=config.TEXT_MODEL),
                json=payload,
                headers={"x-goog-api-key": config.GEMINI_API_KEY},
            )
        if resp.status_code != 200:
            return True
        verdict = json.loads(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
    except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError):
        return True

    animals = verdict.get("animals")
    in_document = bool(verdict.get("in_document"))
    if in_document or (isinstance(animals, int) and animals > 1):
        log.info("разворот: брак по проверке (животных %s, в документе %s)",
                 animals, in_document)
        return False
    return True


async def render(card: bytes, name: str, serial: str, territory: str, motto: str) -> Image.Image:
    """Полный шаг 2: промпт → edit → проверка → при браке один повтор."""
    prompt = build_prompt(name, serial, territory, motto)
    async with _GATE:
        img = await _edit(card, prompt)
        if await _looks_clean(img):
            return img
        log.info("разворот %s: повтор после брака", serial)
        return await _edit(card, prompt)
