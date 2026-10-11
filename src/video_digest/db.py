"""SQLite storage (plain sqlite3): videos and LLM analyses. Tables are created on connect."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from video_digest import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    channel TEXT,
    url TEXT NOT NULL,
    upload_date TEXT,
    duration_seconds INTEGER,
    source_language TEXT,
    transcript_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id),
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    cache_key TEXT NOT NULL UNIQUE,
    analysis_json TEXT NOT NULL,
    hints_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL
);
CREATE INDEX IF NOT EXISTS analyses_video ON analyses (video_id, prompt_version, model);
"""


class VideoRow(BaseModel):
    id: str
    title: str
    channel: str | None
    url: str
    upload_date: str | None
    duration_seconds: int | None
    source_language: str | None
    transcript_path: str


class AnalysisRow(BaseModel):
    video_id: str
    prompt_version: str
    model: str
    cache_key: str
    analysis_json: str
    hints_json: str
    created_at: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None


def connect(path: Path = config.DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_video(conn: sqlite3.Connection, video: VideoRow) -> None:
    conn.execute(
        """INSERT INTO videos (id, title, channel, url, upload_date, duration_seconds, source_language, transcript_path)
        VALUES (:id, :title, :channel, :url, :upload_date, :duration_seconds, :source_language, :transcript_path)
        ON CONFLICT(id) DO UPDATE SET title=excluded.title, channel=excluded.channel, url=excluded.url,
            upload_date=excluded.upload_date, duration_seconds=excluded.duration_seconds,
            source_language=COALESCE(excluded.source_language, videos.source_language),
            transcript_path=excluded.transcript_path""",
        video.model_dump(),
    )
    conn.commit()


def get_video(conn: sqlite3.Connection, video_id: str) -> VideoRow | None:
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    return VideoRow(**dict(row)) if row else None


def has_cache_key(conn: sqlite3.Connection, cache_key: str) -> bool:
    return conn.execute("SELECT 1 FROM analyses WHERE cache_key = ?", (cache_key,)).fetchone() is not None


def save_analysis(conn: sqlite3.Connection, row: AnalysisRow) -> None:
    """Replaces earlier analyses of the same video, prompt version and model."""
    data = row.model_dump()
    data["created_at"] = row.created_at or datetime.now(UTC).isoformat(timespec="seconds")
    with conn:
        conn.execute(
            "DELETE FROM analyses WHERE (video_id = ? AND prompt_version = ? AND model = ?) OR cache_key = ?",
            (row.video_id, row.prompt_version, row.model, row.cache_key),
        )
        conn.execute(
            """INSERT INTO analyses (video_id, prompt_version, model, cache_key, analysis_json, hints_json,
                created_at, tokens_in, tokens_out, cost_usd)
            VALUES (:video_id, :prompt_version, :model, :cache_key, :analysis_json, :hints_json,
                :created_at, :tokens_in, :tokens_out, :cost_usd)""",
            data,
        )


def latest_analyses(conn: sqlite3.Connection, prompt_version: str) -> list[tuple[VideoRow, AnalysisRow]]:
    """Newest analysis per video for a prompt version (any model), newest uploads first."""
    rows = conn.execute(
        """SELECT v.id AS v_id, v.title, v.channel, v.url, v.upload_date, v.duration_seconds,
                v.source_language, v.transcript_path, a.*
        FROM analyses a JOIN videos v ON v.id = a.video_id
        WHERE a.prompt_version = ? AND a.id = (
            SELECT a2.id FROM analyses a2 WHERE a2.video_id = a.video_id AND a2.prompt_version = a.prompt_version
            ORDER BY a2.created_at DESC, a2.id DESC LIMIT 1)
        ORDER BY v.upload_date DESC, v.id""",
        (prompt_version,),
    ).fetchall()
    result: list[tuple[VideoRow, AnalysisRow]] = []
    for r in rows:
        d = dict(r)
        video = VideoRow(id=d["v_id"], **{k: d[k] for k in VideoRow.model_fields if k != "id"})
        result.append((video, AnalysisRow(**{k: d[k] for k in AnalysisRow.model_fields})))
    return result
