from typing import Any

import pytest
from conftest import RUSSIAN_SUMMARY
from pydantic import ValidationError

from video_digest.schema import (
    ALLOWED_TIMESTAMPS,
    ANALYSIS_LANGUAGE,
    TOPIC_CHECK,
    TopicRef,
    VideoAnalysis,
    seconds_to_timestamp,
    timestamp_to_seconds,
)


def test_valid_analysis(analysis_data: dict[str, Any]) -> None:
    analysis = VideoAnalysis.model_validate(analysis_data)
    assert analysis.scores.practical_value.score == 7
    assert analysis.verdict == "summary_enough"


@pytest.mark.parametrize("ts", ["0:00", "2:00", "02:00", "59:59", "1:02:05", "00:02:00", "12:00:00"])
def test_timestamp_regex_accepts(analysis_data: dict[str, Any], ts: str) -> None:
    analysis_data["key_points"][0]["timestamp"] = ts
    VideoAnalysis.model_validate(analysis_data)


@pytest.mark.parametrize("ts", ["2", "2:0", "2:60", "60:00", "1:2:05", "1:02:5", "abc", "2:00 ", "-1:00", "123:00:00"])
def test_timestamp_regex_rejects(analysis_data: dict[str, Any], ts: str) -> None:
    analysis_data["key_points"][0]["timestamp"] = ts
    with pytest.raises(ValidationError):
        VideoAnalysis.model_validate(analysis_data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("source_language",), "eng"),
        (("source_language",), "EN"),
        (("verdict",), "maybe"),
        (("demo_confidence",), "medium"),
        (("scores", "demo_value", "score"), 11),
        (("scores", "demo_value", "score"), -1),
        (("topics",), [{"slug": "mcp", "description": ""}, {"slug": "rag", "description": ""}]),  # fewer than 3
        (("topics",), [{"slug": s, "description": ""} for s in ("AI Agents", "mcp", "rag")]),  # bad slug
        (("topics",), [{"slug": s, "description": ""} for s in ("mcp", "mcp", "rag")]),  # duplicates
        (("topics",), ["mcp", "rag", "memory"]),  # plain strings
        (("key_points",), []),  # fewer than 4
        (("summary",), "Just one sentence."),
        (("verdict_reason",), ""),
    ],
)
def test_invalid_values(analysis_data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    target = analysis_data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        VideoAnalysis.model_validate(analysis_data)


def test_extra_and_missing_fields(analysis_data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        VideoAnalysis.model_validate(analysis_data | {"extra": 1})
    del analysis_data["entities"]
    with pytest.raises(ValidationError):
        VideoAnalysis.model_validate(analysis_data)


def test_too_many_best_moments(analysis_data: dict[str, Any]) -> None:
    analysis_data["best_moments"] = [{"timestamp": "0:00", "why_ru": "Интересно."}] * 4
    with pytest.raises(ValidationError):
        VideoAnalysis.model_validate(analysis_data)


def test_timestamps_must_be_chunk_starts(analysis_data: dict[str, Any]) -> None:
    allowed = {0, 120, 240, 360}
    VideoAnalysis.model_validate(analysis_data, context={ALLOWED_TIMESTAMPS: allowed})
    analysis_data["best_moments"][0]["timestamp"] = "4:30"
    with pytest.raises(ValidationError, match="not chunk heading starts"):
        VideoAnalysis.model_validate(analysis_data, context={ALLOWED_TIMESTAMPS: allowed})
    VideoAnalysis.model_validate(analysis_data)  # no context: only the regex applies


def test_timestamp_conversion() -> None:
    assert timestamp_to_seconds("2:00") == 120
    assert timestamp_to_seconds("00:02:00") == 120
    assert timestamp_to_seconds("1:02:05") == 3725
    assert seconds_to_timestamp(120) == "2:00"
    assert seconds_to_timestamp(3725) == "1:02:05"


def test_json_schema_is_strict_compatible() -> None:
    schema = VideoAnalysis.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    for definition in schema["$defs"].values():
        assert definition["additionalProperties"] is False
        assert set(definition["required"]) == set(definition["properties"])


def test_source_language_any_iso_code(analysis_data: dict[str, Any]) -> None:
    analysis_data["source_language"] = "de"
    assert VideoAnalysis.model_validate(analysis_data).source_language == "de"


def test_language_check_latin_target(analysis_data: dict[str, Any]) -> None:
    VideoAnalysis.model_validate(analysis_data, context={ANALYSIS_LANGUAGE: "en"})
    VideoAnalysis.model_validate(analysis_data, context={ANALYSIS_LANGUAGE: "fr"})  # lenient: no Latin-language check
    analysis_data["summary"] = RUSSIAN_SUMMARY
    with pytest.raises(ValidationError, match="must be written in English"):
        VideoAnalysis.model_validate(analysis_data, context={ANALYSIS_LANGUAGE: "en"})


def test_language_check_cyrillic_target(analysis_data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="no Cyrillic"):
        VideoAnalysis.model_validate(analysis_data, context={ANALYSIS_LANGUAGE: "ru"})
    analysis_data["summary"] = RUSSIAN_SUMMARY  # contains Latin terms (Claude Mem, OpenCode, token): fine
    VideoAnalysis.model_validate(analysis_data, context={ANALYSIS_LANGUAGE: "ru"})
    VideoAnalysis.model_validate(analysis_data, context={ANALYSIS_LANGUAGE: "uk"})


def test_language_check_allows_a_few_cyrillic_words(analysis_data: dict[str, Any]) -> None:
    analysis_data["summary"] += " The author quotes the word «пример»."
    VideoAnalysis.model_validate(analysis_data, context={ANALYSIS_LANGUAGE: "en"})


def test_topic_check_hook(analysis_data: dict[str, Any]) -> None:
    seen: list[list[str]] = []

    def check(refs: list[TopicRef]) -> None:
        seen.append([r.slug for r in refs])
        raise ValueError("topics: too many new topics")

    with pytest.raises(ValidationError, match="too many new topics"):
        VideoAnalysis.model_validate(analysis_data, context={TOPIC_CHECK: check})
    assert seen == [["coding-agents", "memory", "ai-tools"]]
