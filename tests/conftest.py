"""Shared test data. All tests are offline."""

import copy
from typing import Any

import pytest

VALID_ANALYSIS: dict[str, Any] = {
    "source_language": "en",
    "summary": (
        "The author connects Claude Mem to OpenCode. "
        "The plugin keeps agent memory between sessions. "
        "Installation is a single terminal command. "
        "The author then compares token usage before and after. "
        "The savings are roughly tenfold."
    ),
    "key_points": [
        {"text": "The agent forgets context between sessions.", "timestamp": "0:00"},
        {"text": "Claude Mem stores memory in SQLite.", "timestamp": "2:00"},
        {"text": "One npx command installs it.", "timestamp": "4:00"},
        {"text": "Token usage drops tenfold.", "timestamp": "6:00"},
    ],
    "topics": [
        {"slug": "coding-agents", "description": "AI agents that write and edit code"},
        {"slug": "memory", "description": "Persistent memory for LLM agents"},
        {"slug": "ai-tools", "description": "Tools built on AI models"},
    ],
    "entities": ["OpenCode", "Claude Mem", "SQLite"],
    "scores": {
        "practical_value": {"score": 7, "justification": "Concrete install steps."},
        "information_density": {"score": 6, "justification": "Steady signal."},
        "demo_value": {"score": 4, "justification": "Some terminal work described."},
    },
    "demo_confidence": "high",
    "needs_screen": False,
    "needs_screen_evidence": "Commands are spoken in full.",
    "best_moments": [{"timestamp": "4:00", "why": "Step-by-step installation."}],
    "verdict": "summary_enough",
    "verdict_reason": "The main ideas fit in the summary.",
}

RUSSIAN_SUMMARY = (
    "Автор подключает Claude Mem к OpenCode. "
    "Плагин хранит память агента между сессиями. "
    "Установка занимает одну команду в терминале. "
    "Затем автор сравнивает расход token до и после. "
    "Экономия получается примерно десятикратной."
)


@pytest.fixture
def analysis_data() -> dict[str, Any]:
    return copy.deepcopy(VALID_ANALYSIS)
