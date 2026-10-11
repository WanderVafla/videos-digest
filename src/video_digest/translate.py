"""Display translation (review files, later the digest). Stored analyses are never translated."""

import hashlib
import sqlite3
from collections.abc import Callable

from pydantic import BaseModel

from video_digest import config, db
from video_digest.languages import language_name
from video_digest.llm import LLMResult, Message, llm
from video_digest.prompts import fill, load_glossary, load_prompt_sections

TRANSLATION_PROMPT_VERSION = "translate_v1"

LLMTextFn = Callable[[list[Message], str], LLMResult[BaseModel]]  # (messages, model) -> result


def translation_cache_key(text: str, target_language: str, model: str) -> str:
    return hashlib.sha256(f"{text}\0{target_language}\0{model}\0{TRANSLATION_PROMPT_VERSION}".encode()).hexdigest()


def translation_messages(text: str, target_language: str) -> list[Message]:
    values = {"LANGUAGE_NAME": language_name(target_language), "GLOSSARY": "\n".join(load_glossary())}
    system = fill(load_prompt_sections(TRANSLATION_PROMPT_VERSION)["SYSTEM"], values)
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


def _default_llm(messages: list[Message], model: str) -> LLMResult[BaseModel]:
    return llm(messages, model)


def translate(
    text: str,
    target_language: str,
    source_language: str = config.ANALYSIS_LANGUAGE,
    *,
    conn: sqlite3.Connection | None = None,
    model: str | None = None,
    llm_fn: LLMTextFn = _default_llm,
) -> str:
    """Translate `text` (written in source_language, the analysis language) to target_language.

    No call when the languages match or the text is empty; results are cached in `translations`.
    """
    if target_language == source_language or not text.strip():
        return text
    model = model or config.LLM_MODEL_FAST
    own_conn = conn is None
    conn = conn or db.connect()
    try:
        key = translation_cache_key(text, target_language, model)
        cached = db.get_translation(conn, key)
        if cached is not None:
            return cached
        result = llm_fn(translation_messages(text, target_language), model)
        translated = result.content.strip()
        if not translated:
            raise ValueError("empty translation")
        db.save_translation(conn, key, target_language, model, translated)
        return translated
    finally:
        if own_conn:
            conn.close()
