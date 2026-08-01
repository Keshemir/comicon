"""Квиз: четыре вопроса, весовые баллы, детерминированная ничья."""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import yaml

from .. import config

_DATA = yaml.safe_load((config.CONTENT / "quiz.yaml").read_text(encoding="utf-8"))
QUESTIONS = _DATA["questions"]
TIE_ORDER = _DATA["tie_break_order"]


@dataclass
class Session:
    """Состояние прохождения. Живёт в памяти — переживать рестарт не обязано."""

    step: int = 0
    scores: dict[str, int] | None = None
    photo: bytes | None = None
    name: str | None = None
    lang: str = config.DEFAULT_LANG

    def __post_init__(self) -> None:
        if self.scores is None:
            self.scores = {}

    def answer(self, option_index: int) -> None:
        for totem, points in QUESTIONS[self.step]["options"][option_index]["scores"].items():
            self.scores[totem] = self.scores.get(totem, 0) + points
        self.step += 1

    @property
    def finished(self) -> bool:
        return self.step >= len(QUESTIONS)


def question(step: int, lang: str) -> tuple[str, list[str]]:
    q = QUESTIONS[step]
    text = q["text"].get(lang) or q["text"]["ru"]
    options = [o["text"].get(lang) or o["text"]["ru"] for o in q["options"]]
    return text, options


def resolve(scores: dict[str, int], user_id: int) -> str:
    """Побеждает максимум. Ничья разрешается по user_id, а не случайно —
    иначе один и тот же гость при повторном проходе получал бы разный тотем."""
    if not scores:
        return TIE_ORDER[zlib.crc32(str(user_id).encode()) % len(TIE_ORDER)]

    best = max(scores.values())
    leaders = [t for t in TIE_ORDER if scores.get(t, 0) == best]
    return leaders[zlib.crc32(str(user_id).encode()) % len(leaders)]
