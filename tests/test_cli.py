"""CLI wiring with fakes: prune, export-review --model, summarize stop on fatal API errors. No network."""

import sqlite3
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from typer.testing import CliRunner

from video_digest import cli, db, review, summarize, transcripts
from video_digest.summarize import PROMPT_VERSION, Outcome, RunStopped

runner = CliRunner()


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "db.sqlite"
    real_connect = db.connect
    monkeypatch.setattr(db, "connect", lambda: real_connect(path))
    return path


def store(conn: sqlite3.Connection, vid: str, prompt_version: str, model: str, cost: float | None, data: str) -> None:
    db.upsert_video(conn, db.VideoRow(id=vid, title=f"Title {vid}", channel=None, url=f"https://youtu.be/{vid}",
                                      upload_date="2026-05-01", duration_seconds=60, source_language="en",
                                      transcript_path="p"))  # fmt: skip
    db.save_analysis(conn, db.AnalysisRow(video_id=vid, prompt_version=prompt_version, model=model,
                                          cache_key=f"{vid}-{prompt_version}-{model}", analysis_json=data,
                                          hints_json="{}", cost_usd=cost, analysis_language="en"))  # fmt: skip


def count(path: Path, prompt_version: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM analyses WHERE prompt_version = ?", (prompt_version,)).fetchone()[0]
    finally:
        conn.close()


def test_prune_dry_run_then_yes(db_path: Path) -> None:
    conn = db.connect()
    store(conn, "aaaaaaaaaaa", "summarize_v1", "m1", 0.002, '{"topics": ["a"]}')
    store(conn, "bbbbbbbbbbb", "summarize_v1", "m2", None, '{"topics": ["b"]}')
    store(conn, "aaaaaaaaaaa", PROMPT_VERSION, "m1", 0.003, "{}")
    conn.close()

    result = runner.invoke(cli.app, ["prune", "--prompt-version", "summarize_v1"])
    assert result.exit_code == 0, result.output
    assert "2 analyses of summarize_v1; models: m1 (1), m2 (1); total cost $0.0020" in result.output
    assert "Dry run" in result.output
    assert count(db_path, "summarize_v1") == 2

    result = runner.invoke(cli.app, ["prune", "--prompt-version", "summarize_v1", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Deleted 2 analyses of summarize_v1." in result.output
    assert (count(db_path, "summarize_v1"), count(db_path, PROMPT_VERSION)) == (0, 1)

    result = runner.invoke(cli.app, ["prune", "--prompt-version", "summarize_v1", "--yes"])
    assert result.exit_code == 0 and "No analyses" in result.output


def test_prune_refuses_current_version(db_path: Path) -> None:
    conn = db.connect()
    store(conn, "aaaaaaaaaaa", PROMPT_VERSION, "m1", 0.003, "{}")
    conn.close()
    result = runner.invoke(cli.app, ["prune", "--prompt-version", PROMPT_VERSION, "--yes"])
    assert result.exit_code == 1
    assert "Refusing" in result.output
    assert count(db_path, PROMPT_VERSION) == 1


def test_export_review_model_option(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_export(conn: sqlite3.Connection, prompt_version: str, **kwargs: Any) -> int:
        calls.append({"prompt_version": prompt_version, **kwargs})
        return 3

    monkeypatch.setattr(review, "export_review", fake_export)
    monkeypatch.setattr(cli.config, "DIGEST_LANGUAGE", "en")
    monkeypatch.setattr(cli.config, "LLM_MODEL_FAST", "fast/model")

    result = runner.invoke(cli.app, ["export-review"])
    assert result.exit_code == 0, result.output
    assert f"Exporting: model=fast/model prompt_version={PROMPT_VERSION}" in result.output
    result = runner.invoke(cli.app, ["export-review", "--model", "smart/model"])
    assert f"model=smart/model prompt_version={PROMPT_VERSION}" in result.output
    monkeypatch.setattr(cli.config, "LLM_MODEL_FAST", "")
    result = runner.invoke(cli.app, ["export-review"])
    assert "model=any" in result.output
    assert [c["model"] for c in calls] == ["fast/model", "smart/model", None]


@pytest.mark.parametrize(("status", "hint"), [(401, "LLM_API_KEY"), (402, "balance"), (403, "access to the model")])
def test_summarize_stops_on_fatal_error(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, hint: str
) -> None:
    monkeypatch.setattr(cli.config, "LLM_MODEL_FAST", "fast/model")
    monkeypatch.setattr(cli.config, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(cli.config, "ANALYSIS_LANGUAGE", "en")
    monkeypatch.setattr(transcripts, "load_transcripts", lambda: [])
    request = httpx2.Request("POST", "https://llm.test/v1/chat/completions")
    error = openai.APIStatusError("denied", response=httpx2.Response(status, request=request), body=None)

    def fake_summarize_all(*args: Any, **kwargs: Any) -> list[Outcome]:
        done = [Outcome(video_id="aaaaaaaaaaa", status="ok"), Outcome(video_id="bbbbbbbbbbb", status="error")]
        raise RunStopped("ccccccccccc", done, error)

    monkeypatch.setattr(summarize, "summarize_all", fake_summarize_all)
    result = runner.invoke(cli.app, ["summarize"])
    assert result.exit_code == 2
    assert f"stopped on ccccccccccc: HTTP {status}" in result.output
    assert "1 done and saved, 1 failed before it" in result.output
    assert "hint:" in result.output and hint in result.output
    assert "sk-test" not in result.output
