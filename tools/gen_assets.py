#!/usr/bin/env python3
"""Разовая генерация ассетов: фактура пергамента + демо-портреты по тотемам.

Портреты нужны только для демонстрации рендера — на бою сюда приходит
фото гостя. Всё льётся параллельно, потому что одна стилизация идёт ~30 c.

    python3 tools/gen_assets.py
"""

import asyncio
import base64
import pathlib
import re
import sys
import time

import httpx
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "generated"
GEN = "https://api.openai.com/v1/images/generations"
EDIT = "https://api.openai.com/v1/images/edits"
MODEL = "gpt-image-2"

PARCHMENT = (
    "A blank sheet of aged cream parchment paper, photographed flat from directly above. "
    "Subtle fiber texture, faint water stains and soft age-toning towards the edges, "
    "very slight mottling. Completely empty — no text, no lettering, no drawing, "
    "no border, no frame, no ornament, no objects, no shadows. "
    "Uniform even lighting, warm ivory tone. Plain background texture only."
)

# Разные люди, чтобы четыре паспорта не выглядели одним гостем.
FACES = {
    "at": "a young Central Asian man in his early twenties, short black hair, slight smile",
    "berkut": "a Central Asian man around forty, strong cheekbones, neutral serious expression",
    "tazy": "a young Central Asian woman in her twenties, long dark hair, calm expression",
    "irbis": "a Central Asian teenager, about seventeen, short hair, faint smile",
}
FACE_BASE = (
    "A plain passport-style photograph of {desc}, looking straight at the camera, "
    "flat neutral grey studio background, even lighting, head and shoulders, "
    "ordinary snapshot, color photo, no props, no hat."
)


def load_key() -> str:
    m = re.search(r"^OPENAI_API_KEY=(.+)$", (ROOT / ".env").read_text(), re.M)
    if not m or not m.group(1).strip():
        sys.exit("OPENAI_API_KEY не найден в .env")
    return m.group(1).strip()


def cost_of(usage: dict) -> float:
    """gpt-image-2: $5/1M текст-вход, $8/1M картинка-вход, $30/1M выход."""
    d = usage.get("input_tokens_details", {})
    return (
        d.get("text_tokens", 0) / 1e6 * 5
        + d.get("image_tokens", 0) / 1e6 * 8
        + usage.get("output_tokens", 0) / 1e6 * 30
    )


async def generate(client: httpx.AsyncClient, prompt: str, name: str, size: str) -> float:
    resp = await client.post(
        GEN, json={"model": MODEL, "prompt": prompt, "size": size, "n": 1}, timeout=300
    )
    if resp.status_code != 200:
        print(f"  {name:16} ОШИБКА {resp.status_code} {resp.text[:140]}")
        return 0.0
    body = resp.json()
    (OUT / f"{name}.png").write_bytes(base64.b64decode(body["data"][0]["b64_json"]))
    cost = cost_of(body.get("usage", {}))
    print(f"  {name:16} готов  ${cost:.4f}")
    return cost


async def stylize(client: httpx.AsyncClient, totem: str, prompt: str) -> float:
    src = OUT / f"face_{totem}.png"
    resp = await client.post(
        EDIT,
        files={"image": (src.name, src.read_bytes(), "image/png")},
        data={"model": MODEL, "prompt": prompt, "size": "1024x1024", "n": "1"},
        timeout=300,
    )
    if resp.status_code != 200:
        print(f"  styled_{totem:9} ОШИБКА {resp.status_code} {resp.text[:140]}")
        return 0.0
    body = resp.json()
    (OUT / f"styled_{totem}.png").write_bytes(base64.b64decode(body["data"][0]["b64_json"]))
    cost = cost_of(body.get("usage", {}))
    print(f"  styled_{totem:9} готов  ${cost:.4f}")
    return cost


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    prompts = yaml.safe_load((ROOT / "content" / "prompts.yaml").read_text())
    totems = yaml.safe_load((ROOT / "content" / "totems.yaml").read_text())
    base = prompts["stylize_base"]

    started = time.time()
    headers = {"Authorization": f"Bearer {load_key()}"}
    async with httpx.AsyncClient(headers=headers) as client:
        print("фактура и лица:")
        spent = sum(
            await asyncio.gather(
                generate(client, PARCHMENT, "parchment", "1536x1024"),
                *(
                    generate(client, FACE_BASE.format(desc=d), f"face_{t}", "1024x1024")
                    for t, d in FACES.items()
                ),
            )
        )
        print("стилизация под тотемы:")
        spent += sum(
            await asyncio.gather(
                *(stylize(client, t, f"{base} {totems[t]['stylize']}") for t in FACES)
            )
        )

    print(f"\nвсего ${spent:.3f} за {time.time() - started:.0f} c")


if __name__ == "__main__":
    asyncio.run(main())
