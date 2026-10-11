"""Transcript loading, cache key, prompt building and summarize_all with a fake llm. No network."""

import copy
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from video_digest import db, summarize
from video_digest.ingest import Chunk, Track, VideoMeta, render_markdown
from video_digest.llm import LLMResult, Message
from video_digest.schema import ALLOWED_TIMESTAMPS, VideoAnalysis, seconds_to_timestamp
from video_digest.summarize import PROMPT_VERSION, cache_key, split_parts, summarize_all
from video_digest.transcripts import load_transcript, load_transcripts

MODEL = "test/model"


def write_transcript(
    directory: Path,
    video_id: str,
    track: Track | None = Track(lang="en-orig", manual=False),
    texts: tuple[str, ...] = ("Hello and welcome.", "Run npx claude-mem install.", "Thanks for watching."),
    title: str = "OpenCode memory demo",
) -> Path:
    meta = VideoMeta(
        id=video_id,
        title=title,
        channel="Chan",
        upload_date="2026-05-25",
        duration=360,
        language="en-US",
        chapters=[{"title": "Intro", "start_time": 0.0, "end_time": 60.0}],
        webpage_url=f"https://www.youtube.com/watch?v={video_id}",
        subtitles=[],
    )
    chunks = [Chunk(start_ms=i * 120_000, end_ms=(i + 1) * 120_000, text=t) for i, t in enumerate(texts)] if track else []
    path = directory / f"{video_id}.md"
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(meta, track, chunks, 120, datetime(2026, 10, 11, tzinfo=UTC)), encoding="utf-8")
    return path


class FakeLLM:
    def __init__(self, analysis: dict[str, Any], fail_for: set[str] | None = None) -> None:
        self.analysis = analysis
        self.fail_for = fail_for or set()
        self.calls: list[tuple[list[Message], dict[str, Any] | None]] = []

    def __call__(
        self,
        messages: list[Message],
        model: str,
        response_schema: type[VideoAnalysis] | None = None,
        temperature: float | None = 0,
        *,
        validation_context: dict[str, Any] | None = None,
    ) -> LLMResult[VideoAnalysis]:
        self.calls.append((messages, validation_context))
        if any(vid in messages[1]["content"] for vid in self.fail_for):
            raise RuntimeError("provider exploded")
        data = copy.deepcopy(self.analysis)
        if validation_context:  # answer with a timestamp the transcript actually has
            ts = seconds_to_timestamp(min(validation_context[ALLOWED_TIMESTAMPS]))
            for item in data["key_points"] + data["best_moments"]:
                item["timestamp"] = ts
        parsed = VideoAnalysis.model_validate(data, context=validation_context)
        return LLMResult(content="{}", parsed=parsed, model=model, mode="json_schema", tokens_in=1000, tokens_out=100, cost_usd=0.01)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return db.connect(tmp_path / "db.sqlite")


# --- transcripts ------------------------------------------------------------------


def test_load_transcript_reads_stage1_format(tmp_path: Path) -> None:
    t = load_transcript(write_transcript(tmp_path, "aaaaaaaaaaa"))
    assert (t.video_id, t.title, t.duration, t.language, t.transcript_lang) == (
        "aaaaaaaaaaa", "OpenCode memory demo", 360, "en-US", "en-orig"
    )
    assert [c.start_seconds for c in t.chunks] == [0, 120, 240]
    assert t.chunks[1].text == "## [00:02:00 - 00:04:00]\n\nRun npx claude-mem install."
    assert t.chapters == [{"title": "Intro", "start": 0.0, "end": 60.0}]
    assert t.raw_path.name == "aaaaaaaaaaa.en-orig.json3"


def test_load_transcripts_skips_non_ok(tmp_path: Path) -> None:
    write_transcript(tmp_path, "aaaaaaaaaaa")
    write_transcript(tmp_path, "bbbbbbbbbbb", track=None)  # transcript_status: no_transcript
    (tmp_path / "notes.txt").write_text("ignored")
    assert [t.video_id for t in load_transcripts(tmp_path)] == ["aaaaaaaaaaa"]


def test_load_real_transcript_fixture_format(tmp_path: Path) -> None:
    """A real stage-1 file header, to catch drift between writer and reader."""
    text = (
        '---\nvideo_id: "QIwLqXJkX08"\ntitle: "OpenCode Persistent Memory"\nchannel: "AI Stack Engineer"\n'
        'url: "https://www.youtube.com/watch?v=QIwLqXJkX08"\nupload_date: "2026-05-25"\nduration: 552\n'
        'language: "en-US"\ntranscript_status: "ok"\ntranscript_lang: "en-orig"\ntranscript_manual: false\n'
        'chunk_seconds: 120\nchunks: 2\nchapters: []\nsubtitles: []\ningested_at: "2026-10-11T08:07:49+00:00"\n---\n\n'
        "# OpenCode Persistent Memory\n\n## [00:00:00 - 00:02:00]\n\nSo, you build something.\n\n"
        "## [01:02:00 - 01:04:00]\n\nLater.\n"
    )
    path = tmp_path / "QIwLqXJkX08.md"
    path.write_text(text, encoding="utf-8")
    t = load_transcript(path)
    assert [c.start_seconds for c in t.chunks] == [0, 3720]
    assert t.transcript_status == "ok"


# --- cache key and prompt ------------------------------------------------------------------


def test_cache_key() -> None:
    base = cache_key("text", "v1", "m")
    assert base == cache_key("text", "v1", "m")
    assert len({base, cache_key("text2", "v1", "m"), cache_key("text", "v2", "m"), cache_key("text", "v1", "m2")}) == 4
    assert cache_key("ab", "c", "m") != cache_key("a", "bc", "m")  # separators prevent collisions


def test_build_messages_contains_rules_glossary_hints(tmp_path: Path) -> None:
    t = load_transcript(write_transcript(tmp_path, "aaaaaaaaaaa"))
    hints = summarize.compute_hints(t.body, t.title, t.duration_seconds, t.raw_path)
    system, user = summarize.build_messages(t, hints, t.chunks)
    assert "do not invent facts" in system["content"].lower()
    assert "Claude Mem\nMCP" in system["content"]  # glossary block
    assert "{{" not in system["content"] + user["content"]
    assert '"command_mentions": 1' in user["content"]
    assert "## [00:02:00 - 00:04:00]" in user["content"]
    assert "0:00 Intro" in user["content"]


def test_prompt_mentions_required_rules() -> None:
    sections = summarize.load_prompt_sections()
    assert set(sections) == {"SYSTEM", "USER", "MERGE"}
    system = sections["SYSTEM"]
    for phrase in ["cap demo_value at 5", "sponsor", "watch_full", "9-10: almost every minute", "{{GLOSSARY}}"]:
        assert phrase in system
    assert summarize.load_glossary()[:3] == ["OpenCode", "Claude Code", "Claude Mem"]


def test_split_parts() -> None:
    from video_digest.transcripts import TranscriptChunk

    chunks = [TranscriptChunk(start_seconds=i * 120, text="x" * 40) for i in range(5)]
    parts = split_parts(chunks, max_chars=100)
    assert [[c.start_seconds for c in p] for p in parts] == [[0, 120], [240, 360], [480]]


# --- summarize_all ------------------------------------------------------------------


def test_summarize_saves_and_caches(tmp_path: Path, conn: sqlite3.Connection, analysis_data: dict[str, Any]) -> None:
    write_transcript(tmp_path, "aaaaaaaaaaa")
    transcripts = load_transcripts(tmp_path)
    fake = FakeLLM(analysis_data)

    outcomes = summarize_all(conn, transcripts, MODEL, llm_fn=fake)
    assert [(o.status, o.verdict, o.tokens_in, o.cost_usd) for o in outcomes] == [("ok", "summary_enough", 1000, 0.01)]
    assert fake.calls[0][1] == {ALLOWED_TIMESTAMPS: {0, 120, 240}}

    row = conn.execute("SELECT * FROM analyses").fetchone()
    assert (row["video_id"], row["prompt_version"], row["model"]) == ("aaaaaaaaaaa", PROMPT_VERSION, MODEL)
    assert row["cache_key"] == cache_key(transcripts[0].body, PROMPT_VERSION, MODEL)
    assert (row["tokens_in"], row["tokens_out"], row["cost_usd"]) == (1000, 100, 0.01)
    assert json.loads(row["analysis_json"])["verdict"] == "summary_enough"
    assert json.loads(row["hints_json"])["command_mentions"] == 1
    video = db.get_video(conn, "aaaaaaaaaaa")
    assert video is not None and video.source_language == "en" and video.duration_seconds == 360

    # second run: cached, no API call
    assert summarize_all(conn, transcripts, MODEL, llm_fn=fake) == []
    assert len(fake.calls) == 1

    # other model or --force: new call; force overwrites instead of adding rows
    summarize_all(conn, transcripts, "other/model", llm_fn=fake)
    summarize_all(conn, transcripts, MODEL, force=True, llm_fn=fake)
    assert len(fake.calls) == 3
    assert conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 2


def test_changed_transcript_invalidates_cache(tmp_path: Path, conn: sqlite3.Connection, analysis_data: dict[str, Any]) -> None:
    write_transcript(tmp_path, "aaaaaaaaaaa")
    fake = FakeLLM(analysis_data)
    summarize_all(conn, load_transcripts(tmp_path), MODEL, llm_fn=fake)
    write_transcript(tmp_path, "aaaaaaaaaaa", texts=("Different.", "Text.", "Here."))
    assert len(summarize_all(conn, load_transcripts(tmp_path), MODEL, llm_fn=fake)) == 1
    assert conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 1  # replaced, not duplicated


def test_limit_id_and_error_continues(tmp_path: Path, conn: sqlite3.Connection, analysis_data: dict[str, Any]) -> None:
    for vid in ("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"):
        write_transcript(tmp_path, vid, title=f"Video {vid}")
    transcripts = load_transcripts(tmp_path)

    only = summarize_all(conn, transcripts, MODEL, only_id="bbbbbbbbbbb", llm_fn=FakeLLM(analysis_data))
    assert [o.video_id for o in only] == ["bbbbbbbbbbb"]

    fake = FakeLLM(analysis_data, fail_for={"Video aaaaaaaaaaa"})
    outcomes = summarize_all(conn, transcripts, MODEL, limit=5, llm_fn=fake)
    assert [(o.video_id, o.status) for o in outcomes] == [("aaaaaaaaaaa", "error"), ("ccccccccccc", "ok")]
    assert "provider exploded" in outcomes[0].detail

    limited = summarize_all(conn, transcripts, MODEL, limit=1, force=True, llm_fn=FakeLLM(analysis_data))
    assert len(limited) == 1


def test_long_transcript_is_split_and_merged(
    tmp_path: Path, conn: sqlite3.Connection, analysis_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    texts = tuple("word " * 2000 for _ in range(4))
    write_transcript(tmp_path, "aaaaaaaaaaa", texts=texts)
    t = load_transcripts(tmp_path)[0]
    system_chars = len(summarize.system_message()["content"])
    monkeypatch.setattr(summarize, "MAX_PROMPT_TOKENS", int((system_chars + 25_000) / summarize.CHARS_PER_TOKEN))
    fake = FakeLLM(analysis_data)

    result = summarize.analyze(t, MODEL, fake)
    assert len(fake.calls) >= 3  # at least 2 parts + merge
    part_contexts = [ctx for _, ctx in fake.calls[:-1]]
    part_starts = [ctx[ALLOWED_TIMESTAMPS] for ctx in part_contexts if ctx]
    assert sorted(s for starts in part_starts for s in starts) == [0, 120, 240, 360]  # parts cover all chunks once
    assert "part 1 of" in fake.calls[0][0][1]["content"]
    assert "Partial analyses" in fake.calls[-1][0][1]["content"]
    assert fake.calls[-1][1] == {ALLOWED_TIMESTAMPS: {0, 120, 240, 360}}
    assert result.tokens_in == 1000 * len(fake.calls)
    assert result.cost_usd == pytest.approx(0.01 * len(fake.calls))
