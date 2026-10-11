"""Stage 2: per-video summary and scores with the LLM, cached in SQLite by transcript hash."""

import hashlib
import json
import sqlite3
from collections.abc import Callable
from importlib import resources
from typing import Any, Protocol

from pydantic import BaseModel

from video_digest import db
from video_digest.features import DemoHints, compute_hints
from video_digest.llm import LLMResult, Message, llm
from video_digest.schema import ALLOWED_TIMESTAMPS, VideoAnalysis, seconds_to_timestamp
from video_digest.transcripts import Transcript, TranscriptChunk

PROMPT_VERSION = "summarize_v1"
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
    analysis: VideoAnalysis
    hints: DemoHints
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None


class Outcome(BaseModel):
    video_id: str
    status: str  # "ok", "cached", "error"
    detail: str = ""
    verdict: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None


# --- prompt ------------------------------------------------------------------


def load_prompt_sections(version: str = PROMPT_VERSION) -> dict[str, str]:
    """Split prompts/<version>.md into its "=== NAME ===" sections."""
    text = resources.files("video_digest.prompts").joinpath(f"{version}.md").read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    name: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("=== ") and line.endswith(" ==="):
            if name:
                sections[name] = "\n".join(lines).strip()
            name, lines = line[4:-4].strip(), []
        else:
            lines.append(line)
    if name:
        sections[name] = "\n".join(lines).strip()
    return sections


def load_glossary() -> list[str]:
    text = resources.files("video_digest.prompts").joinpath("glossary.txt").read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def fill(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def format_duration(seconds: int | None) -> str:
    return seconds_to_timestamp(seconds) if seconds else "unknown"


def _metadata(t: Transcript, hints: DemoHints) -> dict[str, str]:
    chapters = [f"{seconds_to_timestamp(int(c.get('start') or 0))} {c.get('title')}" for c in t.chapters]
    return {
        "TITLE": t.title,
        "CHANNEL": t.channel or "unknown",
        "UPLOAD_DATE": t.upload_date or "unknown",
        "DURATION": format_duration(t.duration_seconds),
        "LANGUAGE": t.language or "unknown",
        "CHAPTERS": "; ".join(chapters) if chapters else "none",
        "HINTS": hints.model_dump_json(indent=2),
    }


def system_message() -> Message:
    system = fill(load_prompt_sections()["SYSTEM"], {"GLOSSARY": "\n".join(load_glossary())})
    return {"role": "system", "content": system}


def build_messages(
    t: Transcript,
    hints: DemoHints,
    chunks: list[TranscriptChunk],
    part: tuple[int, int] | None = None,
) -> list[Message]:
    values = _metadata(t, hints)
    values["TRANSCRIPT"] = "\n\n".join(c.text for c in chunks)
    values["PART_NOTE"] = (
        f"This is part {part[0]} of {part[1]} of the transcript; analyze only this part.\n\n" if part else ""
    )
    return [system_message(), {"role": "user", "content": fill(load_prompt_sections()["USER"], values)}]


def build_merge_messages(t: Transcript, hints: DemoHints, partials: list[VideoAnalysis]) -> list[Message]:
    values = _metadata(t, hints)
    values["PART_COUNT"] = str(len(partials))
    values["PARTIALS"] = "\n\n".join(p.model_dump_json() for p in partials)
    return [system_message(), {"role": "user", "content": fill(load_prompt_sections()["MERGE"], values)}]


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


def cache_key(transcript_text: str, prompt_version: str, model: str) -> str:
    return hashlib.sha256(f"{transcript_text}\0{prompt_version}\0{model}".encode()).hexdigest()


def _context(chunks: list[TranscriptChunk]) -> dict[str, Any]:
    return {ALLOWED_TIMESTAMPS: {c.start_seconds for c in chunks}}


def analyze(t: Transcript, model: str, llm_fn: LLMFn = llm) -> Analysis:
    """One request for the whole transcript; above MAX_PROMPT_TOKENS: per-part requests + a merge."""
    hints = compute_hints(t.body, t.title, t.duration_seconds, t.raw_path)
    messages = build_messages(t, hints, t.chunks)
    results: list[LLMResult[VideoAnalysis]] = []

    if estimate_tokens(messages) <= MAX_PROMPT_TOKENS:
        results.append(llm_fn(messages, model, VideoAnalysis, validation_context=_context(t.chunks)))
    else:
        overhead = sum(len(m["content"]) for m in messages) - len(t.body)
        max_chars = int(MAX_PROMPT_TOKENS * CHARS_PER_TOKEN) - overhead
        parts = split_parts(t.chunks, max(max_chars, 10_000))
        for i, part in enumerate(parts, 1):
            part_messages = build_messages(t, hints, part, (i, len(parts)))
            results.append(llm_fn(part_messages, model, VideoAnalysis, validation_context=_context(part)))
        partials = [r.parsed for r in results if r.parsed]
        merge_messages = build_merge_messages(t, hints, partials)
        results.append(llm_fn(merge_messages, model, VideoAnalysis, validation_context=_context(t.chunks)))

    final = results[-1].parsed
    if final is None:
        raise ValueError("LLM returned no analysis")
    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    return Analysis(
        analysis=final,
        hints=hints,
        tokens_in=sum(r.tokens_in for r in results),
        tokens_out=sum(r.tokens_out for r in results),
        cost_usd=sum(costs) if costs else None,
    )


def video_row(t: Transcript, source_language: str | None = None) -> db.VideoRow:
    language = source_language or ((t.language or "").split("-")[0] or None)
    return db.VideoRow(
        id=t.video_id,
        title=t.title,
        channel=t.channel,
        url=t.url,
        upload_date=t.upload_date,
        duration_seconds=t.duration_seconds,
        source_language=language,
        transcript_path=str(t.path),
    )


def summarize_all(
    conn: sqlite3.Connection,
    transcripts: list[Transcript],
    model: str,
    *,
    force: bool = False,
    limit: int | None = None,
    only_id: str | None = None,
    llm_fn: LLMFn = llm,
    on_start: Callable[[int, int, Transcript], None] | None = None,
    on_result: Callable[[Outcome], None] | None = None,
) -> list[Outcome]:
    """Analyze transcripts lacking an analysis for (transcript, PROMPT_VERSION, model).

    `limit` counts videos that need an API call; cached ones are skipped silently.
    A failure on one video is recorded and the run continues.
    """
    selected = [t for t in transcripts if only_id is None or t.video_id == only_id]
    todo: list[tuple[Transcript, str]] = []
    for t in selected:
        key = cache_key(t.body, PROMPT_VERSION, model)
        if force or not db.has_cache_key(conn, key):
            todo.append((t, key))
    if limit is not None:
        todo = todo[:limit]

    outcomes: list[Outcome] = []
    for i, (t, key) in enumerate(todo, 1):
        if on_start:
            on_start(i, len(todo), t)
        db.upsert_video(conn, video_row(t))
        try:
            result = analyze(t, model, llm_fn)
        except Exception as exc:
            outcome = Outcome(video_id=t.video_id, status="error", detail=f"{type(exc).__name__}: {exc}")
        else:
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
                ),
            )
            outcome = Outcome(
                video_id=t.video_id,
                status="ok",
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
