"""Weak, text-only demo signals passed to the LLM as hints. Pure functions, no LLM."""

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

POINTER_PATTERNS_EN = [
    r"\bas you can see\b",
    r"\bhere we\b",
    r"\blet me show\b",
    r"\bon the screen\b",
    r"\bon my screen\b",
    r"\bclick(?:s|ed|ing)?\b",
    r"\bopen the\b",
    r"\btype in\b",
    r"\bright here\b",
]
POINTER_PATTERNS_RU = [
    r"\bсмотрите\b",
    r"\bвидите\b",
    r"\bвот здесь\b",
    r"\bпокажу\b",
    r"\bкликаем\b",
    r"\bоткрываем\b",
    r"\bнажимаем\b",
    r"\bвводим\b",
]
POINTER_RE = re.compile("|".join(POINTER_PATTERNS_EN + POINTER_PATTERNS_RU), re.IGNORECASE)

FILE_EXTENSIONS = "py|js|ts|tsx|jsx|json|md|ya?ml|toml|sh|txt|env|sql|html|css|go|rs|lock|cfg|ini"
COMMAND_PATTERNS = [
    r"\b(?:npx|npm|pip|uv|git|docker|cd)\b",  # shell tools
    r"\bdash dash\b",  # spoken "--"
    r"(?<![\w-])--\s?[a-z][\w-]*",  # --flags, also "-- ide" from normalized captions
    rf"\b[\w-]+\.(?:{FILE_EXTENSIONS})\b",  # file names with extensions
]
COMMAND_RE = re.compile("|".join(COMMAND_PATTERNS), re.IGNORECASE)

TITLE_KEYWORDS = [
    "tutorial", "walkthrough", "demo", "hands-on", "hands on", "live coding", "setup", "set up",
    "step by step", "step-by-step", "how to", "guide", "install", "build",
    "туториал", "гайд", "демо", "пошагов", "настройк", "установк", "лайвкодинг", "как сделать",
    "как настроить", "с нуля", "разбор", "практик",
]  # fmt: skip

PAUSE_THRESHOLD_SECONDS = 8.0


class PauseStats(BaseModel):
    count: int
    total_seconds: float
    share_of_duration: float | None  # None when the duration is unknown


class DemoHints(BaseModel):
    pointer_phrases: int
    pointer_phrases_per_min: float
    command_mentions: int
    long_pauses: PauseStats | None  # None when the raw caption file is missing
    title_keywords: list[str]


def count_pointer_phrases(text: str) -> int:
    return len(POINTER_RE.findall(text))


def pointer_phrases_per_min(text: str, duration_seconds: float | None) -> float:
    if not duration_seconds:
        return 0.0
    return round(count_pointer_phrases(text) / (duration_seconds / 60), 2)


def command_mentions(text: str) -> int:
    return len(COMMAND_RE.findall(text))


def speech_intervals(json3: dict[str, Any]) -> list[tuple[float, float]]:
    """(start, end) in seconds of caption events that carry text."""
    intervals: list[tuple[float, float]] = []
    for event in json3.get("events", []):
        text = "".join(s.get("utf8", "") for s in event.get("segs") or [])
        if not text.strip():
            continue
        start = event.get("tStartMs", 0) / 1000
        intervals.append((start, start + event.get("dDurationMs", 0) / 1000))
    return sorted(intervals)


def long_pauses(
    raw_path: Path,
    duration_seconds: float | None,
    threshold: float = PAUSE_THRESHOLD_SECONDS,
) -> PauseStats | None:
    """Gaps longer than `threshold` between speech events; None if the raw file is missing."""
    if not raw_path.exists():
        return None
    intervals = speech_intervals(json.loads(raw_path.read_text(encoding="utf-8")))
    gaps: list[float] = []
    speech_end: float | None = None
    for start, end in intervals:
        if speech_end is not None and start - speech_end > threshold:
            gaps.append(start - speech_end)
        speech_end = end if speech_end is None else max(speech_end, end)
    total = round(sum(gaps), 1)
    share = round(total / duration_seconds, 3) if duration_seconds else None
    return PauseStats(count=len(gaps), total_seconds=total, share_of_duration=share)


def title_keywords(title: str, description: str = "") -> list[str]:
    haystack = f"{title}\n{description}".lower()
    return [kw for kw in TITLE_KEYWORDS if kw in haystack]


def compute_hints(
    text: str,
    title: str,
    duration_seconds: float | None,
    raw_path: Path,
    description: str = "",
) -> DemoHints:
    pointers = count_pointer_phrases(text)
    return DemoHints(
        pointer_phrases=pointers,
        pointer_phrases_per_min=pointer_phrases_per_min(text, duration_seconds),
        command_mentions=command_mentions(text),
        long_pauses=long_pauses(raw_path, duration_seconds),
        title_keywords=title_keywords(title, description),
    )
