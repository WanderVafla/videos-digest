import json
from pathlib import Path

import pytest

from video_digest.features import (
    command_mentions,
    compute_hints,
    count_pointer_phrases,
    long_pauses,
    pointer_phrases_per_min,
    speech_intervals,
    title_keywords,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_pointer_phrases_english() -> None:
    text = "As you can see, here we have the config. Let me show you. Click Save, then open the terminal and type in the key right here."
    assert count_pointer_phrases(text) == 7


def test_pointer_phrases_russian() -> None:
    text = "Смотрите, вот здесь кнопка. Видите? Сейчас покажу: кликаем, открываем настройки, нажимаем «Сохранить» и вводим ключ."
    assert count_pointer_phrases(text) == 8


def test_pointer_phrases_whole_words_only() -> None:
    assert count_pointer_phrases("The clickbait thumbnail; рассмотрите вопрос; where we are") == 0


def test_pointer_phrases_per_min() -> None:
    assert pointer_phrases_per_min("смотрите смотрите click", 120) == 1.5
    assert pointer_phrases_per_min("смотрите", None) == 0.0


def test_command_mentions() -> None:
    text = (
        "Run npx claude-mem install -- ide, then uv sync and git push. "
        "Use docker compose, cd into the folder, edit config.json and main.py. "
        "Pass --force. Then pip install dash dash upgrade."
    )
    # npx, -- ide, uv, git, docker, cd, config.json, main.py, --force, pip, dash dash
    assert command_mentions(text) == 11


def test_command_mentions_ignores_prose() -> None:
    assert command_mentions("We discussed the future of AI agents and models.") == 0
    assert command_mentions("Мы обсудили будущее агентов") == 0


def test_long_pauses_from_fixture() -> None:
    stats = long_pauses(FIXTURES / "pauses.ru.json3", duration_seconds=565)
    assert stats is not None
    assert stats.count == 2
    assert stats.total_seconds == pytest.approx(26.2)
    assert stats.share_of_duration == pytest.approx(0.046)


def test_long_pauses_none_without_gaps() -> None:
    stats = long_pauses(FIXTURES / "sample.ru.json3", duration_seconds=1424)
    assert stats is not None and stats.count == 0 and stats.total_seconds == 0


def test_long_pauses_missing_file(tmp_path: Path) -> None:
    assert long_pauses(tmp_path / "missing.json3", 100) is None


def test_long_pauses_threshold_and_unknown_duration(tmp_path: Path) -> None:
    events = [
        {"tStartMs": 0, "dDurationMs": 999999},  # window setup, no segs
        {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "a"}]},
        {"tStartMs": 2000, "dDurationMs": 500, "segs": [{"utf8": "\n"}]},  # whitespace only: ignored
        {"tStartMs": 9000, "dDurationMs": 1000, "segs": [{"utf8": "b"}]},  # 7 s gap: below threshold
        {"tStartMs": 20000, "dDurationMs": 1000, "segs": [{"utf8": "c"}]},  # 10 s gap
    ]
    path = tmp_path / "x.json3"
    path.write_text(json.dumps({"events": events}))
    assert speech_intervals({"events": events}) == [(0, 2), (9, 10), (20, 21)]
    stats = long_pauses(path, None)
    assert stats is not None
    assert (stats.count, stats.total_seconds, stats.share_of_duration) == (1, 10.0, None)


def test_title_keywords() -> None:
    assert title_keywords("Claude Code Tutorial: full setup") == ["tutorial", "setup"]
    assert title_keywords("Гайд по Pi: настройка с нуля") == ["гайд", "настройк", "с нуля"]
    assert title_keywords("AI news of the week") == []
    assert title_keywords("Agents", description="A hands-on walkthrough") == ["walkthrough", "hands-on"]


def test_compute_hints(tmp_path: Path) -> None:
    hints = compute_hints("Смотрите, запускаем npx и git.", "Demo", 60, tmp_path / "none.json3")
    assert hints.pointer_phrases == 1
    assert hints.pointer_phrases_per_min == 1.0
    assert hints.command_mentions == 2
    assert hints.long_pauses is None
    assert hints.title_keywords == ["demo"]
