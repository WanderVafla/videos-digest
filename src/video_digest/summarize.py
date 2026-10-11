"""Stage 2: per-video summary and scores with the LLM, cached in SQLite by transcript hash."""

import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from video_digest import config, db, topics
from video_digest.features import DemoHints, compute_hints
from video_digest.languages import base_code, effective_language, language_name
from video_digest.llm import LLMResult, Message, llm
from video_digest.prompts import fill, load_glossary, load_prompt_sections
from video_digest.schema import (
    ALLOWED_TIMESTAMPS,
    ANALYSIS_LANGUAGE,
    TOPIC_CHECK,
    TopicRef,
    VideoAnalysis,
    seconds_to_timestamp,
)
from video_digest.transcripts import Transcript, TranscriptChunk

PROMPT_VERSION = "summarize_v2"
CHARS_PER_TOKEN = 3.5
MAX_PROMPT_TOKENS = 100_000  # above this the transcript is split into parts


class LLMFn(Protocol):
    def __call__(
        self,
        messages: list[Message],
        model: str,
        response_schema: type[VideoAnalysis] | None = None,
        temperature: float | None = 0,
        *,
        validation_context: dict[str, Any] | None = None,
    ) -> LLMResult[VideoAnalysis]: ...


class Analysis(BaseModel):
    analysis: VideoAnalysis  # topics already resolved against topics.txt
    hints: DemoHints
    new_topics: dict[str, str] = {}
    mapped_topics: list[tuple[str, str]] = []
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None


class Outcome(BaseModel):
    video_id: str
    status: str  # "ok", "error"
    detail: str = ""
    verdict: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None


class TopicState(BaseModel):
    """topics.txt contents plus usage counts from stored analyses, at the start of a video."""

    path: Path
    known: dict[str, str]
    usage: Counter[str]

    @classmethod
    def load(cls, path: Path, conn: sqlite3.Connection) -> "TopicState":
        return cls(path=path, known=topics.load_topics(path), usage=topics.stored_usage(conn))


# --- prompt ------------------------------------------------------------------


def format_duration(seconds: int | None) -> str:
    return seconds_to_timestamp(seconds) if seconds else "unknown"


def source_language_of(t: Transcript) -> str | None:
    return base_code(t.language) or base_code(t.transcript_lang)


def analysis_language_for(t: Transcript, setting: str | None = None) -> str:
    """The effective ANALYSIS_LANGUAGE for this transcript ("source" -> the video's language)."""
    return effective_language(setting or config.ANALYSIS_LANGUAGE, source_language_of(t))


def _metadata(t: Transcript, hints: DemoHints, language: str) -> dict[str, str]:
    chapters = [f"{seconds_to_timestamp(int(c.get('start') or 0))} {c.get('title')}" for c in t.chapters]
    return {
        "TITLE": t.title,
        "CHANNEL": t.channel or "unknown",
        "UPLOAD_DATE": t.upload_date or "unknown",
        "DURATION": format_duration(t.duration_seconds),
        "LANGUAGE": t.language or "unknown",
        "CHAPTERS": "; ".join(chapters) if chapters else "none",
        "HINTS": hints.model_dump_json(indent=2),
        "LANGUAGE_NAME": language_name(language),
    }


def system_message(language: str, topic_block: str) -> Message:
    values = {
        "GLOSSARY": "\n".join(load_glossary()),
        "LANGUAGE_NAME": language_name(language),
        "TOPICS": topic_block,
    }
    return {"role": "system", "content": fill(load_prompt_sections(PROMPT_VERSION)["SYSTEM"], values)}


def build_messages(
    t: Transcript,
    hints: DemoHints,
    chunks: list[TranscriptChunk],
    language: str,
    topic_block: str,
    part: tuple[int, int] | None = None,
) -> list[Message]:
    values = _metadata(t, hints, language)
    values["TRANSCRIPT"] = "\n\n".join(c.text for c in chunks)
    values["PART_NOTE"] = (
        f"This is part {part[0]} of {part[1]} of the transcript; analyze only this part.\n\n" if part else ""
    )
    user = fill(load_prompt_sections(PROMPT_VERSION)["USER"], values)
    return [system_message(language, topic_block), {"role": "user", "content": user}]


def build_merge_messages(
    t: Transcript, hints: DemoHints, partials: list[VideoAnalysis], language: str, topic_block: str
) -> list[Message]:
    values = _metadata(t, hints, language)
    values["PART_COUNT"] = str(len(partials))
    values["PARTIALS"] = "\n\n".join(p.model_dump_json() for p in partials)
    user = fill(load_prompt_sections(PROMPT_VERSION)["MERGE"], values)
    return [system_message(language, topic_block), {"role": "user", "content": user}]


def estimate_tokens(messages: list[Message]) -> int:
    return int(sum(len(m["content"]) for m in messages) / CHARS_PER_TOKEN)


def split_parts(chunks: list[TranscriptChunk], max_chars: int) -> list[list[TranscriptChunk]]:
    """Consecutive groups of chunks, each at most max_chars of text (a single huge chunk stays alone)."""
    parts: list[list[TranscriptChunk]] = []
    current: list[TranscriptChunk] = []
    size = 0
    for chunk in chunks:
        if current and size + len(chunk.text) > max_chars:
            parts.append(current)
            current, size = [], 0
        current.append(chunk)
        size += len(chunk.text)
    if current:
        parts.append(current)
    return parts


# --- analysis ------------------------------------------------------------------


def cache_key(transcript_text: str, prompt_version: str, model: str, analysis_language: str) -> str:
    """The topic list is deliberately not part of the key (it is mutable state)."""
    raw = f"{transcript_text}\0{prompt_version}\0{model}\0{analysis_language}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _context(chunks: list[TranscriptChunk], language: str, known_topics: dict[str, str]) -> dict[str, Any]:
    check: Callable[[list[TopicRef]], None] = partial(topics.check_new_topics, existing=known_topics)
    return {
        ALLOWED_TIMESTAMPS: {c.start_seconds for c in chunks},
        ANALYSIS_LANGUAGE: language,
        TOPIC_CHECK: check,
    }


def apply_topics(analysis: VideoAnalysis, known: dict[str, str]) -> tuple[VideoAnalysis, topics.Resolution]:
    """Map proposed slugs onto topics.txt; returns the analysis with canonical topics."""
    resolution = topics.resolve(analysis.topics, known)
    refs = [TopicRef(slug=s, description=known.get(s) or resolution.new.get(s, "")) for s in resolution.slugs]
    return analysis.model_copy(update={"topics": refs}), resolution


def analyze(
    t: Transcript,
    model: str,
    language: str,
    topic_state: TopicState,
    llm_fn: LLMFn = llm,
) -> Analysis:
    """One request for the whole transcript; above MAX_PROMPT_TOKENS: per-part requests + a merge."""
    hints = compute_hints(t.body, t.title, t.duration_seconds, t.raw_path)
    topic_block = topics.prompt_block(topic_state.known, topic_state.usage)
    known = topic_state.known
    messages = build_messages(t, hints, t.chunks, language, topic_block)
    results: list[LLMResult[VideoAnalysis]] = []

    if estimate_tokens(messages) <= MAX_PROMPT_TOKENS:
        results.append(llm_fn(messages, model, VideoAnalysis, validation_context=_context(t.chunks, language, known)))
    else:
        overhead = sum(len(m["content"]) for m in messages) - len(t.body)
        max_chars = int(MAX_PROMPT_TOKENS * CHARS_PER_TOKEN) - overhead
        parts = split_parts(t.chunks, max(max_chars, 10_000))
        for i, part in enumerate(parts, 1):
            part_messages = build_messages(t, hints, part, language, topic_block, (i, len(parts)))
            context = _context(part, language, known)
            results.append(llm_fn(part_messages, model, VideoAnalysis, validation_context=context))
        partials = [r.parsed for r in results if r.parsed]
        merge_messages = build_merge_messages(t, hints, partials, language, topic_block)
        context = _context(t.chunks, language, known)
        results.append(llm_fn(merge_messages, model, VideoAnalysis, validation_context=context))

    final = results[-1].parsed
    if final is None:
        raise ValueError("LLM returned no analysis")
    resolved, resolution = apply_topics(final, known)
    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    return Analysis(
        analysis=resolved,
        hints=hints,
        new_topics=resolution.new,
        mapped_topics=resolution.mapped,
        tokens_in=sum(r.tokens_in for r in results),
        tokens_out=sum(r.tokens_out for r in results),
        cost_usd=sum(costs) if costs else None,
    )


def video_row(t: Transcript, source_language: str | None = None) -> db.VideoRow:
    return db.VideoRow(
        id=t.video_id,
        title=t.title,
        channel=t.channel,
        url=t.url,
        upload_date=t.upload_date,
        duration_seconds=t.duration_seconds,
        source_language=source_language or source_language_of(t),
        transcript_path=str(t.path),
    )


def _topic_note(result: Analysis) -> str:
    notes = [f"new topic {slug}" for slug in result.new_topics]
    notes += [f"topic {a} -> {b}" for a, b in result.mapped_topics]
    return "; ".join(notes)


def summarize_all(
    conn: sqlite3.Connection,
    transcripts: list[Transcript],
    model: str,
    *,
    analysis_language: str | None = None,
    topics_path: Path | None = None,
    force: bool = False,
    limit: int | None = None,
    only_id: str | None = None,
    llm_fn: LLMFn = llm,
    on_start: Callable[[int, int, Transcript], None] | None = None,
    on_result: Callable[[Outcome], None] | None = None,
) -> list[Outcome]:
    """Analyze transcripts lacking an analysis for (transcript, PROMPT_VERSION, model, language).

    Videos are processed sequentially; new topics are appended to topics.txt after each video,
    so results depend on processing order. `limit` counts videos that need an API call.
    A failure on one video is recorded and the run continues.
    """
    setting = analysis_language or config.ANALYSIS_LANGUAGE
    topics_path = topics_path or config.TOPICS_FILE
    selected = [t for t in transcripts if only_id is None or t.video_id == only_id]
    todo: list[tuple[Transcript, str, str]] = []
    for t in selected:
        language = analysis_language_for(t, setting)
        key = cache_key(t.body, PROMPT_VERSION, model, language)
        if force or not db.has_cache_key(conn, key):
            todo.append((t, key, language))
    if limit is not None:
        todo = todo[:limit]

    outcomes: list[Outcome] = []
    for i, (t, key, language) in enumerate(todo, 1):
        if on_start:
            on_start(i, len(todo), t)
        db.upsert_video(conn, video_row(t))
        try:
            result = analyze(t, model, language, TopicState.load(topics_path, conn), llm_fn)
        except Exception as exc:
            outcome = Outcome(video_id=t.video_id, status="error", detail=f"{type(exc).__name__}: {exc}")
        else:
            topics.append_topics(topics_path, result.new_topics)
            db.upsert_video(conn, video_row(t, result.analysis.source_language))
            db.save_analysis(
                conn,
                db.AnalysisRow(
                    video_id=t.video_id,
                    prompt_version=PROMPT_VERSION,
                    model=model,
                    cache_key=key,
                    analysis_json=result.analysis.model_dump_json(),
                    hints_json=result.hints.model_dump_json(),
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    cost_usd=result.cost_usd,
                    analysis_language=language,
                ),
            )
            outcome = Outcome(
                video_id=t.video_id,
                status="ok",
                detail=_topic_note(result),
                verdict=result.analysis.verdict,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_usd=result.cost_usd,
            )
        outcomes.append(outcome)
        if on_result:
            on_result(outcome)
    return outcomes


def parse_analysis(row: db.AnalysisRow) -> VideoAnalysis:
    return VideoAnalysis.model_validate(json.loads(row.analysis_json))
