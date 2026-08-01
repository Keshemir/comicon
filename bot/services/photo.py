"""Фото гостя: проверка лица, стилизация под тотем, запасной локальный режим.

Исходник нигде не сохраняется — работаем в памяти и отдаём готовый кадр.
"""

from __future__ import annotations

import asyncio
import base64
import io

import cv2
import httpx
import numpy as np
import yaml
from PIL import Image, ImageEnhance, ImageOps

from .. import config

EDIT = "https://api.openai.com/v1/images/edits"
_PROMPTS = yaml.safe_load((config.CONTENT / "prompts.yaml").read_text(encoding="utf-8"))
_GATE = asyncio.Semaphore(config.CONCURRENCY)

# YuNet, а не хааровский каскад: в OpenCV 5 CascadeClassifier выпилен из
# биндингов, а YuNet к тому же заметно точнее — держит поворот головы,
# наклон и мелкие лица. Модель весит 230 КБ и работает на CPU.
_MODEL = config.ROOT / "assets" / "models" / "yunet.onnx"
_DETECTOR = None


def _detector():
    global _DETECTOR
    if _DETECTOR is None:
        if not _MODEL.exists():
            raise RuntimeError(f"нет модели детектора: {_MODEL}")
        _DETECTOR = cv2.FaceDetectorYN.create(str(_MODEL), "", (320, 320), 0.6, 0.3, 5000)
    return _DETECTOR


class NoFace(Exception):
    """На фото не нашлось лица. Проверяем до API — мусор не должен стоить денег."""


def find_face(raw: bytes) -> tuple[int, int, int, int]:
    """Возвращает рамку самого крупного лица или бросает NoFace."""
    array = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise NoFace("не удалось прочитать изображение")

    height, width = image.shape[:2]
    scale = min(1.0, 1024 / max(height, width))     # детектору хватает 1024 по длинной
    if scale < 1.0:
        image = cv2.resize(image, (round(width * scale), round(height * scale)))

    det = _detector()
    det.setInputSize((image.shape[1], image.shape[0]))
    _, faces = det.detect(image)
    if faces is None or len(faces) == 0:
        raise NoFace("лицо не найдено")

    best = max(faces, key=lambda f: f[2] * f[3])    # несколько лиц — берём крупнейшее
    return tuple(round(v / scale) for v in best[:4])


def crop_to_portrait(raw: bytes, face: tuple[int, int, int, int]) -> Image.Image:
    """Кадрирует под пропорцию рамки на бланке, оставляя воздух над головой."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    x, y, w, h = face

    cx, cy = x + w / 2, y + h * 0.52
    target = 492 / 596                      # пропорция окна фото на макете
    half_h = min(h * 2.5, img.height / 2)
    half_w = half_h * target
    if half_w > img.width / 2:
        half_w = img.width / 2
        half_h = half_w / target

    box = (max(cx - half_w, 0), max(cy - half_h, 0),
           min(cx + half_w, img.width), min(cy + half_h, img.height))
    return img.crop(tuple(round(v) for v in box)).resize((1024, 1024), Image.LANCZOS)


def sepia(img: Image.Image) -> Image.Image:
    """Локальный режим: гравюрой не станет, но в бланк садится и стоит ноль."""
    grey = ImageOps.grayscale(img)
    grey = ImageEnhance.Contrast(grey).enhance(1.22)
    arr = np.asarray(grey, dtype=np.float32) / 255.0
    tone = np.stack([arr * 236 + 12, arr * 214 + 10, arr * 176 + 8], axis=-1)
    rng = np.random.default_rng(7)
    tone += rng.normal(0, 4.5, tone.shape[:2])[..., None]
    return Image.fromarray(np.clip(tone, 0, 255).astype(np.uint8))


async def stylize(img: Image.Image, totem_prompt: str) -> tuple[Image.Image, bool]:
    """Стилизует через OpenAI. Возвращает (кадр, дошли ли до модели).

    Ретраи и молчаливый откат на сепию: на стенде живая очередь, и уйти без
    паспорта хуже, чем уйти с паспортом попроще.
    """
    if not config.AI_STYLIZE or not config.OPENAI_API_KEY:
        return sepia(img), False

    buf = io.BytesIO()
    img.save(buf, "PNG")
    prompt = f"{_PROMPTS['stylize_base']} {totem_prompt}"

    async with _GATE:
        for attempt in range(config.AI_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=180) as client:
                    resp = await client.post(
                        EDIT,
                        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                        files={"image": ("face.png", buf.getvalue(), "image/png")},
                        data={"model": config.IMAGE_MODEL, "prompt": prompt,
                              "size": "1024x1024", "n": "1"},
                    )
                if resp.status_code == 200:
                    payload = resp.json()["data"][0]["b64_json"]
                    return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB"), True
                if resp.status_code in (400, 403):
                    break                       # модерация — ретраить бессмысленно
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1.5 * (attempt + 1))

    return sepia(img), False
