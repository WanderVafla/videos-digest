"""topics.txt handling: loading, matching, near-duplicates, appending, merge. No network."""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from video_digest import db, topics
from video_digest.schema import TopicRef


def refs(*items: tuple[str, str]) -> list[TopicRef]:
    return [TopicRef(slug=slug, description=desc) for slug, desc in items]


def test_missing_or_empty_file_is_empty_taxonomy(tmp_path: Path) -> None:
    assert topics.load_topics(tmp_path / "missing.txt") == {}
    (tmp_path / "topics.txt").write_text("")
    assert topics.load_topics(tmp_path / "topics.txt") == {}
    assert "invent the first ones" in topics.prompt_block({}, topics.usage_counts([]))


def test_load_topics_skips_invalid_lines(tmp_path: Path) -> None:
    path = tmp_path / "topics.txt"
    path.write_text("mcp: Model Context Protocol\n\n# comment\nBad Slug: x\nrag:Retrieval: augmented\nnocolon\nmcp: dup\n")
    assert topics.load_topics(path) == {"mcp": "Model Context Protocol", "rag": "Retrieval: augmented"}


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("mcp", ("mcp", "exact")),
        ("coding-agents", ("coding-agent", "normalized")),  # trailing plural s
        ("local-llms", ("local-llm", "normalized")),
        ("codingagent", ("coding-agent", "near")),  # ratio >= 0.85
        ("vibe-coding", (None, "new")),
        ("rag-pipeline", (None, "new")),  # narrower variant: code cannot tell, prompt forbids it
    ],
)
def test_find_existing(slug: str, expected: tuple[str | None, str]) -> None:
    existing = ["mcp", "coding-agent", "local-llm", "rag"]
    assert topics.find_existing(slug, existing) == expected


def test_normalize() -> None:
    assert topics.normalize("AI Agents") == "ai-agent"
    assert topics.normalize("local_llms") == "local-llm"
    assert topics.normalize("business") == "business"  # "ss" is not a plural
    assert topics.normalize("rags") == "rag"
    assert topics.normalize("ss") == "ss"


def test_resolve_maps_and_collects_new() -> None:
    existing = {"coding-agent": "Agents that code", "mcp": "Model Context Protocol"}
    r = topics.resolve(
        refs(("coding-agents", ""), ("mcp", ""), ("vibe-coding", "Prompt-driven development"), ("codingagent", "")),
        existing,
    )
    assert r.slugs == ["coding-agent", "mcp", "vibe-coding"]  # duplicates collapsed
    assert r.new == {"vibe-coding": "Prompt-driven development"}
    assert r.mapped == [("coding-agents", "coding-agent"), ("codingagent", "coding-agent")]


def test_check_new_topics_rules() -> None:
    existing = {"mcp": "Model Context Protocol", "rag": "Retrieval-augmented generation"}
    topics.check_new_topics(refs(("mcp", ""), ("rag", ""), ("vibe-coding", "d"), ("local-llm", "d")), existing)
    with pytest.raises(ValueError, match="at most 2"):
        topics.check_new_topics(refs(("a-b", "d"), ("c-d", "d"), ("e-f", "d")), existing)
    with pytest.raises(ValueError, match="needs a short English description"):
        topics.check_new_topics(refs(("mcp", ""), ("rag", ""), ("vibe-coding", "")), existing)
    with pytest.raises(ValueError, match="more than 3 words"):
        topics.check_new_topics(refs(("mcp", ""), ("rag", ""), ("one-two-three-four", "d")), existing)
    with pytest.raises(ValueError, match="fewer than 3 distinct"):
        topics.check_new_topics(refs(("mcp", ""), ("mcps", ""), ("rag", "")), existing)


def test_first_video_may_seed_more_than_two() -> None:
    topics.check_new_topics(refs(("a", "d"), ("b", "d"), ("c", "d"), ("d", "d")), {})


def test_append_topics_atomic(tmp_path: Path) -> None:
    path = tmp_path / "topics.txt"
    topics.append_topics(path, {"mcp": "Model Context Protocol"})
    topics.append_topics(path, {"rag": "Retrieval", "mcp": "ignored duplicate"})
    assert path.read_text() == "mcp: Model Context Protocol\nrag: Retrieval\n"
    assert not (tmp_path / "topics.txt.tmp").exists()


def test_prompt_block_order_and_cap() -> None:
    known = {f"t{i}": f"topic {i}" for i in range(200)}
    usage = topics.usage_counts([["t5", "t7"], ["t7"], ["t7", "t7"]])  # counted once per video
    lines = topics.prompt_block(known, usage).splitlines()
    assert len(lines) == topics.MAX_IN_PROMPT
    assert lines[:3] == ["- t7 (3): topic 7", "- t5 (1): topic 5", "- t0 (0): topic 0"]


def store(conn: sqlite3.Connection, vid: str, slugs: list[str], data: dict[str, Any]) -> None:
    db.upsert_video(conn, db.VideoRow(id=vid, title=vid, channel=None, url="u", upload_date=None,
                                      duration_seconds=None, source_language="en", transcript_path="p"))  # fmt: skip
    analysis = dict(data, topics=[{"slug": s, "description": f"desc {s}"} for s in slugs])
    db.save_analysis(conn, db.AnalysisRow(video_id=vid, prompt_version="v", model="m", cache_key=vid,
                                          analysis_json=json.dumps(analysis), hints_json="{}"))  # fmt: skip


def test_merge(tmp_path: Path, analysis_data: dict[str, Any]) -> None:
    conn = db.connect(tmp_path / "db.sqlite")
    path = tmp_path / "topics.txt"
    path.write_text("ai-agent: Agents\nagents: Agents too\nmcp: Model Context Protocol\n")
    store(conn, "v1", ["agents", "mcp"], analysis_data)
    store(conn, "v2", ["agents", "ai-agent", "mcp"], analysis_data)
    store(conn, "v3", ["mcp"], analysis_data)

    assert topics.merge(conn, path, "agents", "ai-agent") == 2
    stored = [json.loads(j)["topics"] for j in db.latest_analysis_json_per_video(conn)]
    slugs = sorted(tuple(t["slug"] for t in ts) for ts in stored)
    assert slugs == [("ai-agent", "mcp"), ("ai-agent", "mcp"), ("mcp",)]
    assert {"slug": "ai-agent", "description": "Agents"} in stored[0] + stored[1]
    assert topics.load_topics(path) == {"ai-agent": "Agents", "mcp": "Model Context Protocol"}
    assert topics.stored_usage(conn) == {"ai-agent": 2, "mcp": 3}


def test_merge_into_unknown_slug_renames(tmp_path: Path, analysis_data: dict[str, Any]) -> None:
    conn = db.connect(tmp_path / "db.sqlite")
    path = tmp_path / "topics.txt"
    path.write_text("llms: Large language models\n")
    store(conn, "v1", ["llms"], analysis_data)
    topics.merge(conn, path, "llms", "llm-models")
    assert topics.load_topics(path) == {"llm-models": "Large language models"}
    with pytest.raises(ValueError, match="invalid slug"):
        topics.merge(conn, path, "llm-models", "Bad Slug")
