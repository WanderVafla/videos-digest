"""Pydantic schemas for LLM outputs. VideoAnalysis doubles as the structured-output JSON Schema."""

import re
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationInfo, field_validator, model_validator

TIMESTAMP_PATTERN = r"^(?:\d{1,2}:[0-5]\d:[0-5]\d|[0-5]?\d:[0-5]\d)$"  # "mm:ss" or "h:mm:ss"
TAG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
SENTENCE_END_RE = re.compile(r"[.!?…]+(?=\s|$)")

Timestamp = Annotated[str, StringConstraints(pattern=TIMESTAMP_PATTERN)]
Tag = Annotated[str, StringConstraints(pattern=TAG_PATTERN)]
Score = Annotated[int, Field(ge=0, le=10)]
Verdict = Literal["watch_full", "summary_enough", "skip"]

# Validation context key: set of allowed timestamps in seconds (chunk heading starts).
ALLOWED_TIMESTAMPS = "allowed_timestamps"


def timestamp_to_seconds(ts: str) -> int:
    seconds = 0
    for part in ts.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def seconds_to_timestamp(seconds: int) -> str:
    """120 -> "2:00", 3725 -> "1:02:05"."""
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _require_russian(value: str) -> str:
    if not CYRILLIC_RE.search(value):
        raise ValueError("must be written in Russian")
    return value


class StrictModel(BaseModel):
    """extra="forbid" -> additionalProperties: false, as strict structured output requires."""

    model_config = ConfigDict(extra="forbid")


class KeyPoint(StrictModel):
    text_ru: str = Field(min_length=1, description="The point, in Russian.")
    timestamp: Timestamp = Field(description='Start of the chunk heading where it is said, "m:ss" or "h:mm:ss".')

    _ru = field_validator("text_ru")(_require_russian)


class ScoreWithReason(StrictModel):
    score: Score
    justification: str = Field(min_length=1, description="One sentence.")


class Scores(StrictModel):
    practical_value: ScoreWithReason
    information_density: ScoreWithReason
    demo_value: ScoreWithReason


class BestMoment(StrictModel):
    timestamp: Timestamp
    why_ru: str = Field(min_length=1, description="Why this moment is worth watching, in Russian.")

    _ru = field_validator("why_ru")(_require_russian)


class VideoAnalysis(StrictModel):
    source_language: Literal["ru", "en"]
    summary_ru: str = Field(description="5-7 sentences in Russian, regardless of the source language.")
    key_points: list[KeyPoint] = Field(min_length=4, max_length=8)
    topics: list[Tag] = Field(min_length=3, max_length=7, description="Lowercase English tags.")
    entities: list[str] = Field(description="Tools, models, companies, libraries; canonical names.")
    scores: Scores
    demo_confidence: Literal["low", "high"]
    needs_screen: bool
    needs_screen_evidence: str = Field(description="Short, no direct quotes.")
    best_moments: list[BestMoment] = Field(max_length=3)
    verdict: Verdict
    verdict_reason_ru: str = Field(min_length=1, description="One sentence in Russian.")

    _ru = field_validator("verdict_reason_ru")(_require_russian)

    @field_validator("summary_ru")
    @classmethod
    def _summary(cls, value: str) -> str:
        _require_russian(value)
        # Loose bounds: abbreviations like "т.е." make exact sentence counting unreliable.
        sentences = len(SENTENCE_END_RE.findall(value.strip()))
        if not 3 <= sentences <= 10:
            raise ValueError(f"must be 5-7 sentences, got about {sentences}")
        return value

    @model_validator(mode="after")
    def _timestamps_from_headings(self, info: ValidationInfo) -> Self:
        context: dict[str, Any] = info.context or {}
        allowed: set[int] | None = context.get(ALLOWED_TIMESTAMPS)
        if allowed is None:
            return self
        used = [kp.timestamp for kp in self.key_points] + [bm.timestamp for bm in self.best_moments]
        bad = sorted({ts for ts in used if timestamp_to_seconds(ts) not in allowed})
        if bad:
            options = ", ".join(seconds_to_timestamp(s) for s in sorted(allowed))
            raise ValueError(f"timestamps {bad} are not chunk heading starts; use one of: {options}")
        return self


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()
