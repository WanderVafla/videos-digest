"""translate(): no-op for the same language, cache in SQLite, prompt rules. Fake LLM only."""

import sqlite3
from pathlib import Path

import pytest
from pydantic import BaseModel

from video_digest import db
from video_digest.llm import LLMResult, Message
from video_digest.translate import (
    translate,
    translation_cache_key,
    translation_messages,
)


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[tuple[list[Message], str]] = []

    def __call__(self, messages: list[Message], model: str) -> LLMResult[BaseModel]:
        self.calls.append((messages, model))
        return LLMResult(content=self.reply, parsed=None, model=model, mode="text")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return db.connect(tmp_path / "db.sqlite")


def test_same_language_returns_text_without_call(conn: sqlite3.Connection) -> None:
    fake = FakeLLM("never")
    assert translate("Hello", "en", "en", conn=conn, model="m", llm_fn=fake) == "Hello"
    assert translate("  ", "ru", "en", conn=conn, model="m", llm_fn=fake) == "  "
    assert fake.calls == []


def test_translation_is_cached(conn: sqlite3.Connection) -> None:
    fake = FakeLLM("  Привет, OpenCode.  ")
    assert translate("Hello, OpenCode.", "ru", "en", conn=conn, model="m", llm_fn=fake) == "Привет, OpenCode."
    assert translate("Hello, OpenCode.", "ru", "en", conn=conn, model="m", llm_fn=fake) == "Привет, OpenCode."
    assert len(fake.calls) == 1
    row = conn.execute("SELECT * FROM translations").fetchone()
    assert (row["target_language"], row["model"], row["text"]) == ("ru", "m", "Привет, OpenCode.")
    assert row["cache_key"] == translation_cache_key("Hello, OpenCode.", "ru", "m")

    translate("Hello, OpenCode.", "fr", "en", conn=conn, model="m", llm_fn=FakeLLM("Bonjour"))
    translate("Hello, OpenCode.", "ru", "en", conn=conn, model="m2", llm_fn=FakeLLM("Привет"))
    assert conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0] == 3


def test_cache_key_parts() -> None:
    keys = {
        translation_cache_key("a", "ru", "m"),
        translation_cache_key("b", "ru", "m"),
        translation_cache_key("a", "fr", "m"),
        translation_cache_key("a", "ru", "m2"),
    }
    assert len(keys) == 4


def test_empty_reply_is_an_error(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="empty translation"):
        translate("Hello", "ru", "en", conn=conn, model="m", llm_fn=FakeLLM("   "))
    assert conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0] == 0


def test_translation_prompt_rules() -> None:
    system, user = translation_messages("- 2:00 Use **RAG** with Qdrant.", "ru")
    text = system["content"]
    assert "into Russian" in text
    assert "NEVER translate terms" in text
    assert "CLI flags, file names, code identifiers, URLs, acronyms" in text
    assert "Keep numbers, timestamps" in text
    assert "Return only the translated text" in text
    assert "Claude Mem\nMCP" in text  # glossary
    assert "{{" not in text
    assert user == {"role": "user", "content": "- 2:00 Use **RAG** with Qdrant."}
