#!/usr/bin/env python3
"""Генерация иллюстраций тотемов под шаблон паспорта.

Картинки делает OpenAI (gpt-image-2), текст в боте — Gemini. Промпты живут
в content/totems.yaml, поле `illustration`; общий стиль — константа STYLE.

    python3 tools/gen_illustration.py berkut
    python3 tools/gen_illustration.py all --size 1536x1024
"""

import argparse
import base64
import pathlib
import re
import sys
import time

import httpx
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "generated"
API = "https://api.openai.com/v1/images/generations"

STYLE = (
    "Vintage engraving-style pencil illustration on aged parchment. "
    "Monochrome sepia and graphite only, absolutely no saturated color. "
    "Fine cross-hatching and stippling, loose sketchy linework, "
    "the kind of drawing found on an antique passport or banknote. "
    "Plain flat cream parchment background, no border, no frame, no text, "
    "no lettering, no signature, no watermark. "
    "Full body, side three-quarter view, the animal fills the frame."
)


def load_key() -> str:
    env = ROOT / ".env"
    if env.exists():
        m = re.search(r"^OPENAI_API_KEY=(.+)$", env.read_text(), re.M)
        if m and m.group(1).strip():
            return m.group(1).strip()
    sys.exit("OPENAI_API_KEY не найден в .env")


def load_prompts() -> dict[str, str]:
    totems = yaml.safe_load((ROOT / "content" / "totems.yaml").read_text())
    return {k: v["illustration"] for k, v in totems.items()}


def generate(totem: str, prompt: str, model: str, size: str, key: str) -> pathlib.Path:
    resp = httpx.post(
        API,
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "prompt": f"{prompt} {STYLE}", "size": size, "n": 1},
        timeout=300,
    )
    if resp.status_code != 200:
        raise SystemExit(f"{totem}: HTTP {resp.status_code} — {resp.text[:300]}")

    body = resp.json()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{totem}.png"
    path.write_bytes(base64.b64decode(body["data"][0]["b64_json"]))

    usage = body.get("usage", {})
    print(
        f"  {totem:8} {path.stat().st_size // 1024:5} КБ  "
        f"out_tokens={usage.get('output_tokens', '?')}"
    )
    return path


def main() -> None:
    prompts = load_prompts()
    parser = argparse.ArgumentParser()
    parser.add_argument("totem", choices=[*prompts, "all"])
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--size", default="1024x1024")
    args = parser.parse_args()

    key = load_key()
    targets = list(prompts) if args.totem == "all" else [args.totem]
    started = time.time()
    for name in targets:
        generate(name, prompts[name], args.model, args.size, key)
    print(f"готово за {time.time() - started:.0f} c")


if __name__ == "__main__":
    main()
