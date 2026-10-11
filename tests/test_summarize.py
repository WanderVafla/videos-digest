"""Transcript loading, cache key, prompt building and summarize_all with a fake llm. No network."""

import copy
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from conftest import RUSSIAN_SUMMARY

from video_digest import db, summarize, topics
from video_digest.ingest import Chunk, Track, VideoMeta, render_markdown
from video_digest.llm import LLMResult, Message
from video_digest.summarize import RunStopped
from video_digest.schema import (
    ALLOWED_TIMESTAMPS,
    ANALYSIS_LANGUAGE,
    TOPIC_CHECK,
    VideoAnalysis,
    seconds_to_timestamp,
)
from video_digest.summarize import (
    PROMPT_VERSION,
    TopicState,
    cache_key,
    split_parts,
    summarize_all,
)
from video_digest.transcripts import TranscriptChunk, load_transcript, load_transcripts

MODEL = "test/model"


def write_transcript(
    directory: Path,
    video_id: str,
    track: Track | None = Track(lang="en-orig", manual=False),
    texts: tuple[str, ...] = ("Hello and welcome.", "Run npx claude-mem install.", "Thanks for watching."),
    title: str = "OpenCode memory demo",
    language: str = "en-US",
) -> Path:
    meta = VideoMeta(
        id=video_id,
        title=title,
        channel="Chan",
        upload_date="2026-05-25",
        duration=360,
        language=language,
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
    """Validates a canned analysis with the real context, like llm() would (no retries)."""

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


@pytest.fixture
def topics_file(tmp_path: Path) -> Path:
    return tmp_path / "topics.txt"


def run(conn: sqlite3.Connection, transcripts: list[Any], topics_file: Path, fake: Any, **kwargs: Any) -> list[Any]:
    kwargs.setdefault("analysis_language", "en")
    return summarize_all(conn, transcripts, MODEL, topics_path=topics_file, llm_fn=fake, **kwargs)


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


# --- cache key, language, prompt ------------------------------------------------------------


def test_cache_key() -> None:
    base = cache_key("text", "v1", "m", "en")
    assert base == cache_key("text", "v1", "m", "en")
    variants = {
        base,
        cache_key("text2", "v1", "m", "en"),
        cache_key("text", "v2", "m", "en"),
        cache_key("text", "v1", "m2", "en"),
        cache_key("text", "v1", "m", "ru"),
    }
    assert len(variants) == 5
    assert cache_key("ab", "c", "m", "en") != cache_key("a", "bc", "m", "en")  # separators prevent collisions


def test_analysis_language_for(tmp_path: Path) -> None:
    t_ru = load_transcript(write_transcript(tmp_path, "aaaaaaaaaaa", language="ru"))
    t_unknown = load_transcript(write_transcript(tmp_path, "bbbbbbbbbbb", language=""))
    assert summarize.analysis_language_for(t_ru, "en") == "en"
    assert summarize.analysis_language_for(t_ru, "source") == "ru"
    assert summarize.analysis_language_for(t_unknown, "source") == "en"  # from transcript_lang "en-orig"


def test_build_messages_contains_rules_glossary_hints_topics(tmp_path: Path) -> None:
    t = load_transcript(write_transcript(tmp_path, "aaaaaaaaaaa"))
    hints = summarize.compute_hints(t.body, t.title, t.duration_seconds, t.raw_path)
    system, user = summarize.build_messages(t, hints, t.chunks, "ru", "- mcp (3): Model Context Protocol")
    assert "do not invent facts" in system["content"].lower()
    assert "Claude Mem\nMCP" in system["content"]  # glossary block
    assert "verdict_reason in\nRussian" in system["content"]
    assert "- mcp (3): Model Context Protocol" in system["content"]
    assert "{{" not in system["content"] + user["content"]
    assert '"command_mentions": 1' in user["content"]
    assert "## [00:02:00 - 00:04:00]" in user["content"]
    assert "0:00 Intro" in user["content"]
    assert "Write the analysis in Russian." in user["content"]


def test_prompt_mentions_required_rules() -> None:
    sections = summarize.load_prompt_sections(PROMPT_VERSION)
    assert set(sections) == {"SYSTEM", "USER", "MERGE"}
    system = sections["SYSTEM"]
    for phrase in [
        "cap demo_value at 5",
        "sponsor",
        "watch_full",
        "9-10: almost every minute",
        "{{GLOSSARY}}",
        "{{TOPICS}}",
        "Never translate terms",
        "Entities and topic slugs are never translated",
        "At most 2 new topics per video",
        "If the list is empty, invent the first topics yourself",
    ]:
        assert phrase in system, phrase
    assert summarize.load_glossary()[:3] == ["OpenCode", "Claude Code", "Claude Mem"]


def test_split_parts() -> None:
    chunks = [TranscriptChunk(start_seconds=i * 120, text="x" * 40) for i in range(5)]
    parts = split_parts(chunks, max_chars=100)
    assert [[c.start_seconds for c in p] for p in parts] == [[0, 120], [240, 360], [480]]


# --- summarize_all ------------------------------------------------------------------


def test_summarize_saves_and_caches(
    tmp_path: Path, conn: sqlite3.Connection, topics_file: Path, analysis_data: dict[str, Any]
) -> None:
    write_transcript(tmp_path, "aaaaaaaaaaa")
    transcripts = load_transcripts(tmp_path)
    fake = FakeLLM(analysis_data)

    outcomes = run(conn, transcripts, topics_file, fake)
    assert [(o.status, o.verdict, o.tokens_in, o.cost_usd) for o in outcomes] == [("ok", "summary_enough", 1000, 0.01)]
    context = fake.calls[0][1]
    assert context is not None
    assert context[ALLOWED_TIMESTAMPS] == {0, 120, 240}
    assert context[ANALYSIS_LANGUAGE] == "en"
    assert TOPIC_CHECK in context

    row = conn.execute("SELECT * FROM analyses").fetchone()
    assert (row["video_id"], row["prompt_version"], row["model"], row["analysis_language"]) == (
        "aaaaaaaaaaa", PROMPT_VERSION, MODEL, "en"
    )
    assert row["cache_key"] == cache_key(transcripts[0].body, PROMPT_VERSION, MODEL, "en")
    assert (row["tokens_in"], row["tokens_out"], row["cost_usd"]) == (1000, 100, 0.01)
    stored = json.loads(row["analysis_json"])
    assert stored["verdict"] == "summary_enough"
    assert [t["slug"] for t in stored["topics"]] == ["coding-agents", "memory", "ai-tools"]
    assert json.loads(row["hints_json"])["command_mentions"] == 1
    video = db.get_video(conn, "aaaaaaaaaaa")
    assert video is not None and video.source_language == "en" and video.duration_seconds == 360

    # first video with an empty taxonomy: its topics become the first entries
    assert topics.load_topics(topics_file) == {
        "coding-agents": "AI agents that write and edit code",
        "memory": "Persistent memory for LLM agents",
        "ai-tools": "Tools built on AI models",
    }
    assert "new topic coding-agents" in outcomes[0].detail

    # second run: cached, no API call
    assert run(conn, transcripts, topics_file, fake) == []
    assert len(fake.calls) == 1

    # other model or --force: new call; force overwrites instead of adding rows
    summarize_all(conn, transcripts, "other/model", analysis_language="en", topics_path=topics_file, llm_fn=fake)
    run(conn, transcripts, topics_file, fake, force=True)
    assert len(fake.calls) == 3
    assert conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 2


def test_language_setting_changes_cache_key(
    tmp_path: Path, conn: sqlite3.Connection, topics_file: Path, analysis_data: dict[str, Any]
) -> None:
    write_transcript(tmp_path, "aaaaaaaaaaa", language="ru")
    transcripts = load_transcripts(tmp_path)
    run(conn, transcripts, topics_file, FakeLLM(analysis_data), analysis_language="en")

    ru_data = dict(analysis_data, summary=RUSSIAN_SUMMARY)
    fake_ru = FakeLLM(ru_data)
    outcomes = run(conn, transcripts, topics_file, fake_ru, analysis_language="source")
    assert [o.status for o in outcomes] == ["ok"]
    assert fake_ru.calls[0][1] is not None and fake_ru.calls[0][1][ANALYSIS_LANGUAGE] == "ru"
    assert "Write the analysis in Russian." in fake_ru.calls[0][0][1]["content"]
    languages = [r["analysis_language"] for r in conn.execute("SELECT analysis_language FROM analyses ORDER BY id")]
    assert languages == ["en", "ru"]

    # the language check is real: an English answer for a Cyrillic target is rejected
    outcomes = run(conn, transcripts, topics_file, FakeLLM(analysis_data), analysis_language="uk")
    assert outcomes[0].status == "error" and "no Cyrillic" in outcomes[0].detail


def test_changed_transcript_invalidates_cache(
    tmp_path: Path, conn: sqlite3.Connection, topics_file: Path, analysis_data: dict[str, Any]
) -> None:
    write_transcript(tmp_path, "aaaaaaaaaaa")
    fake = FakeLLM(analysis_data)
    run(conn, load_transcripts(tmp_path), topics_file, fake)
    write_transcript(tmp_path, "aaaaaaaaaaa", texts=("Different.", "Text.", "Here."))
    assert len(run(conn, load_transcripts(tmp_path), topics_file, fake)) == 1
    assert conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 1  # replaced, not duplicated


def test_limit_id_and_error_continues(
    tmp_path: Path, conn: sqlite3.Connection, topics_file: Path, analysis_data: dict[str, Any]
) -> None:
    for vid in ("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"):
        write_transcript(tmp_path, vid, title=f"Video {vid}")
    transcripts = load_transcripts(tmp_path)

    only = run(conn, transcripts, topics_file, FakeLLM(analysis_data), only_id="bbbbbbbbbbb")
    assert [o.video_id for o in only] == ["bbbbbbbbbbb"]

    fake = FakeLLM(analysis_data, fail_for={"Video aaaaaaaaaaa"})
    outcomes = run(conn, transcripts, topics_file, fake, limit=5)
    assert [(o.video_id, o.status) for o in outcomes] == [("aaaaaaaaaaa", "error"), ("ccccccccccc", "ok")]
    assert "provider exploded" in outcomes[0].detail

    limited = run(conn, transcripts, topics_file, FakeLLM(analysis_data), limit=1, force=True)
    assert len(limited) == 1


def test_topics_are_resolved_against_file(
    tmp_path: Path, conn: sqlite3.Connection, topics_file: Path, analysis_data: dict[str, Any]
) -> None:
    topics_file.write_text("coding-agent: AI agents that write code\nmemory: Agent memory\nmcp: Model Context Protocol\n")
    write_transcript(tmp_path, "aaaaaaaaaaa")
    analysis_data["topics"] = [
        {"slug": "coding-agents", "description": ""},  # plural of an existing slug -> mapped
        {"slug": "memory", "description": ""},  # exact
        {"slug": "vibe-coding", "description": "Building software by prompting an AI"},  # new
    ]
    fake = FakeLLM(analysis_data)
    outcomes = run(conn, load_transcripts(tmp_path), topics_file, fake)
    assert outcomes[0].status == "ok"
    assert outcomes[0].detail == "new topic vibe-coding; topic coding-agents -> coding-agent"
    stored = json.loads(conn.execute("SELECT analysis_json FROM analyses").fetchone()[0])
    assert stored["topics"] == [
        {"slug": "coding-agent", "description": "AI agents that write code"},
        {"slug": "memory", "description": "Agent memory"},
        {"slug": "vibe-coding", "description": "Building software by prompting an AI"},
    ]
    assert list(topics.load_topics(topics_file)) == ["coding-agent", "memory", "mcp", "vibe-coding"]
    # the prompt listed the existing topics
    assert "- mcp (0): Model Context Protocol" in fake.calls[0][0][0]["content"]


def test_too_many_new_topics_is_a_validation_error(
    tmp_path: Path, conn: sqlite3.Connection, topics_file: Path, analysis_data: dict[str, Any]
) -> None:
    topics_file.write_text("mcp: Model Context Protocol\n")
    write_transcript(tmp_path, "aaaaaaaaaaa")
    outcomes = run(conn, load_transcripts(tmp_path), topics_file, FakeLLM(analysis_data))  # 3 new topics
    assert outcomes[0].status == "error" and "at most 2" in outcomes[0].detail
    assert topics.load_topics(topics_file) == {"mcp": "Model Context Protocol"}  # unchanged


def test_topic_usage_in_prompt(conn: sqlite3.Connection, topics_file: Path, analysis_data: dict[str, Any]) -> None:
    topics_file.write_text("memory: Agent memory\nmcp: Model Context Protocol\nrag: Retrieval\n")
    db.upsert_video(conn, db.VideoRow(id="v", title="t", channel=None, url="u", upload_date=None,
                                      duration_seconds=None, source_language="en", transcript_path="p"))  # fmt: skip
    stored = dict(analysis_data, topics=[{"slug": "rag", "description": ""}, {"slug": "mcp", "description": ""}])
    db.save_analysis(conn, db.AnalysisRow(video_id="v", prompt_version=PROMPT_VERSION, model="m", cache_key="k",
                                          analysis_json=json.dumps(stored), hints_json="{}"))  # fmt: skip
    # a newer row of another prompt version (here a legacy v1 row, topics as strings) is ignored
    db.upsert_video(conn, db.VideoRow(id="w", title="t", channel=None, url="u", upload_date=None,
                                      duration_seconds=None, source_language="en", transcript_path="p"))  # fmt: skip
    for vid in ("v", "w"):
        db.save_analysis(conn, db.AnalysisRow(video_id=vid, prompt_version="summarize_v1", model="m", cache_key=f"old-{vid}",
                                              analysis_json=json.dumps({"topics": ["memory", "memory-tools"]}),
                                              hints_json="{}", created_at="2999-01-01T00:00:00+00:00"))  # fmt: skip
    state = TopicState.load(topics_file, conn)
    assert topics.prompt_block(state.known, state.usage).splitlines() == [
        "- mcp (1): Model Context Protocol",
        "- rag (1): Retrieval",
        "- memory (0): Agent memory",
    ]


def test_long_transcript_is_split_and_merged(
    tmp_path: Path, topics_file: Path, analysis_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    texts = tuple("word " * 2000 for _ in range(4))
    write_transcript(tmp_path, "aaaaaaaaaaa", texts=texts)
    t = load_transcripts(tmp_path)[0]
    system_chars = len(summarize.system_message("en", "(empty)")["content"])
    monkeypatch.setattr(summarize, "MAX_PROMPT_TOKENS", int((system_chars + 25_000) / summarize.CHARS_PER_TOKEN))
    fake = FakeLLM(analysis_data)
    state = TopicState(path=topics_file, known={}, usage=topics.usage_counts([]))

    result = summarize.analyze(t, MODEL, "en", state, fake)
    assert len(fake.calls) >= 3  # at least 2 parts + merge
    part_starts = [ctx[ALLOWED_TIMESTAMPS] for _, ctx in fake.calls[:-1] if ctx]
    assert sorted(s for starts in part_starts for s in starts) == [0, 120, 240, 360]  # parts cover all chunks once
    assert "part 1 of" in fake.calls[0][0][1]["content"]
    assert "Partial analyses" in fake.calls[-1][0][1]["content"]
    final_context = fake.calls[-1][1]
    assert final_context is not None and final_context[ALLOWED_TIMESTAMPS] == {0, 120, 240, 360}
    assert result.tokens_in == 1000 * len(fake.calls)
    assert result.cost_usd == pytest.approx(0.01 * len(fake.calls))


@pytest.mark.parametrize("status", [401, 402, 403])
def test_fatal_api_error_stops_the_run(
    tmp_path: Path, conn: sqlite3.Connection, topics_file: Path, analysis_data: dict[str, Any], status: int
) -> None:
    for vid in ("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd"):
        write_transcript(tmp_path, vid, title=f"Video {vid}")
    request = httpx2.Request("POST", "https://llm.test/v1/chat/completions")
    error = openai.APIStatusError("denied", response=httpx2.Response(status, request=request), body=None)

    class FailingLLM(FakeLLM):
        def __call__(self, messages: list[Message], *args: Any, **kwargs: Any) -> LLMResult[VideoAnalysis]:
            if "Video bbbbbbbbbbb" in messages[1]["content"]:
                raise RuntimeError("one bad video")  # ordinary errors: logged, run continues
            if "Video ccccccccccc" in messages[1]["content"]:
                self.calls.append((messages, None))
                raise error
            return super().__call__(messages, *args, **kwargs)

    fake = FailingLLM(analysis_data)
    seen: list[str] = []
    with pytest.raises(RunStopped) as info:
        run(conn, load_transcripts(tmp_path), topics_file, fake, on_result=lambda o: seen.append(o.status))
    stop = info.value
    assert (stop.video_id, stop.status) == ("ccccccccccc", status)
    assert [(o.video_id, o.status) for o in stop.outcomes] == [("aaaaaaaaaaa", "ok"), ("bbbbbbbbbbb", "error")]
    assert seen == ["ok", "error"]
    # the result saved before the stop stays; the video after it was never tried
    assert [r[0] for r in conn.execute("SELECT video_id FROM analyses")] == ["aaaaaaaaaaa"]
    assert not any("Video ddddddddddd" in m[1]["content"] for m, _ in fake.calls)


def test_transient_error_after_retries_does_not_stop_the_run(
    tmp_path: Path, conn: sqlite3.Connection, topics_file: Path, analysis_data: dict[str, Any]
) -> None:
    for vid in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        write_transcript(tmp_path, vid, title=f"Video {vid}")
    request = httpx2.Request("POST", "https://llm.test/v1/chat/completions")
    error = openai.RateLimitError("slow", response=httpx2.Response(429, request=request), body=None)

    class Flaky(FakeLLM):
        def __call__(self, messages: list[Message], *args: Any, **kwargs: Any) -> LLMResult[VideoAnalysis]:
            if "Video aaaaaaaaaaa" in messages[1]["content"]:
                raise error
            return super().__call__(messages, *args, **kwargs)

    outcomes = run(conn, load_transcripts(tmp_path), topics_file, Flaky(analysis_data))
    assert [o.status for o in outcomes] == ["error", "ok"]
