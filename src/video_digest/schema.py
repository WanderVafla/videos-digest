"""Pydantic schemas for LLM outputs. VideoAnalysis doubles as the structured-output JSON Schema.

Context-dependent checks run only when model_validate gets a validation context:
- ALLOWED_TIMESTAMPS: set of seconds (chunk heading starts) every timestamp must be in;
- ANALYSIS_LANGUAGE: ISO 639-1 code the prose must be written in (lenient script check);
- TOPIC_CHECK: callable(list[TopicRef]) raising ValueError (new-topic rules, see topics.py).
"""

import re
from collections.abc import Callable
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from video_digest.languages import check_script

TIMESTAMP_PATTERN = r"^(?:\d{1,2}:[0-5]\d:[0-5]\d|[0-5]?\d:[0-5]\d)$"  # "mm:ss" or "h:mm:ss"
TAG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
LANGUAGE_PATTERN = r"^[a-z]{2}$"  # ISO 639-1
SENTENCE_END_RE = re.compile(r"[.!?…。]+(?=\s|$)")

Timestamp = Annotated[str, StringConstraints(pattern=TIMESTAMP_PATTERN)]
Tag = Annotated[str, StringConstraints(pattern=TAG_PATTERN)]
LanguageCode = Annotated[str, StringConstraints(pattern=LANGUAGE_PATTERN)]
Score = Annotated[int, Field(ge=0, le=10)]
Verdict = Literal["watch_full", "summary_enough", "skip"]

ALLOWED_TIMESTAMPS = "allowed_timestamps"
ANALYSIS_LANGUAGE = "analysis_language"
TOPIC_CHECK = "topic_check"


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


class StrictModel(BaseModel):
    """extra="forbid" -> additionalProperties: false, as strict structured output requires."""

    model_config = ConfigDict(extra="forbid")


class KeyPoint(StrictModel):
    text: str = Field(min_length=1, description="The point, in the analysis language.")
    timestamp: Timestamp = Field(description='Start of the chunk heading where it is said, "m:ss" or "h:mm:ss".')


class ScoreWithReason(StrictModel):
    score: Score
    justification: str = Field(min_length=1, description="One sentence, in the analysis language.")


class Scores(StrictModel):
    practical_value: ScoreWithReason
    information_density: ScoreWithReason
    demo_value: ScoreWithReason


class BestMoment(StrictModel):
    timestamp: Timestamp
    why: str = Field(min_length=1, description="Why this moment is worth watching, in the analysis language.")


class TopicRef(StrictModel):
    slug: Tag = Field(description="Lowercase English kebab-case, 1-3 words; reuse an existing slug when one fits.")
    description: str = Field(description='Short English description; required for a new topic, "" for an existing one.')


class VideoAnalysis(StrictModel):
    source_language: LanguageCode = Field(description="ISO 639-1 code of the transcript language.")
    summary: str = Field(description="5-7 sentences in the analysis language.")
    key_points: list[KeyPoint] = Field(min_length=4, max_length=8)
    topics: list[TopicRef] = Field(min_length=3, max_length=7)
    entities: list[str] = Field(description="Tools, models, companies, libraries; canonical names, never translated.")
    scores: Scores
    demo_confidence: Literal["low", "high"]
    needs_screen: bool
    needs_screen_evidence: str = Field(description="Short, no direct quotes.")
    best_moments: list[BestMoment] = Field(max_length=3)
    verdict: Verdict
    verdict_reason: str = Field(min_length=1, description="One sentence in the analysis language.")

    @field_validator("summary")
    @classmethod
    def _summary_length(cls, value: str) -> str:
        # Loose bounds: abbreviations make exact sentence counting unreliable.
        sentences = len(SENTENCE_END_RE.findall(value.strip()))
        if not 3 <= sentences <= 10:
            raise ValueError(f"must be 5-7 sentences, got about {sentences}")
        return value

    @field_validator("topics")
    @classmethod
    def _unique_topics(cls, value: list[TopicRef]) -> list[TopicRef]:
        slugs = [t.slug for t in value]
        if len(set(slugs)) != len(slugs):
            raise ValueError("topic slugs must be unique")
        return value

    @model_validator(mode="after")
    def _context_checks(self, info: ValidationInfo) -> Self:
        context: dict[str, Any] = info.context or {}
        allowed: set[int] | None = context.get(ALLOWED_TIMESTAMPS)
        if allowed is not None:
            used = [kp.timestamp for kp in self.key_points] + [bm.timestamp for bm in self.best_moments]
            bad = sorted({ts for ts in used if timestamp_to_seconds(ts) not in allowed})
            if bad:
                options = ", ".join(seconds_to_timestamp(s) for s in sorted(allowed))
                raise ValueError(f"timestamps {bad} are not chunk heading starts; use one of: {options}")
        language: str | None = context.get(ANALYSIS_LANGUAGE)
        if language:
            try:
                check_script(self.summary, language)
            except ValueError as exc:
                raise ValueError(f"summary {exc}") from exc
        topic_check: Callable[[list[TopicRef]], None] | None = context.get(TOPIC_CHECK)
        if topic_check:
            topic_check(self.topics)
        return self
