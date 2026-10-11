"""Read stage-1 transcript files: data/transcripts/<id>.md (JSON-valued frontmatter + chunks)."""

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from video_digest import config

HEADING_RE = re.compile(r"^## \[(\d{2}):(\d{2}):(\d{2}) - \d{2}:\d{2}:\d{2}\]$", re.MULTILINE)


class TranscriptChunk(BaseModel):
    start_seconds: int
    text: str  # heading line + text, as in the file


class Transcript(BaseModel):
    path: Path
    video_id: str
    title: str
    channel: str | None
    url: str
    upload_date: str | None
    duration: int | None
    language: str | None
    transcript_status: str
    transcript_lang: str | None
    chunk_seconds: int
    chapters: list[dict[str, Any]]
    body: str  # everything after the frontmatter
    chunks: list[TranscriptChunk]

    @property
    def raw_path(self) -> Path:
        """The json3 caption file stage 1 saved for this transcript (may be missing)."""
        return config.RAW_DIR / f"{self.video_id}.{self.transcript_lang}.json3"

    @property
    def duration_seconds(self) -> int | None:
        if self.duration:
            return self.duration
        if self.chunks:
            return self.chunks[-1].start_seconds + self.chunk_seconds
        return None


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    end = text.index("\n---\n", 4)
    fields: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if line.strip():
            key, value = line.split(": ", 1)
            fields[key] = json.loads(value)
    return fields, text[end + len("\n---\n") :]


def split_chunks(body: str) -> list[TranscriptChunk]:
    matches = list(HEADING_RE.finditer(body))
    chunks: list[TranscriptChunk] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        chunks.append(TranscriptChunk(start_seconds=start, text=body[m.start() : end].strip()))
    return chunks


def load_transcript(path: Path) -> Transcript:
    fields, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return Transcript(
        path=path,
        video_id=fields["video_id"],
        title=fields.get("title") or "",
        channel=fields.get("channel"),
        url=fields.get("url") or f"https://www.youtube.com/watch?v={fields['video_id']}",
        upload_date=fields.get("upload_date"),
        duration=fields.get("duration"),
        language=fields.get("language"),
        transcript_status=fields.get("transcript_status", ""),
        transcript_lang=fields.get("transcript_lang"),
        chunk_seconds=fields.get("chunk_seconds") or 120,
        chapters=fields.get("chapters") or [],
        body=body,
        chunks=split_chunks(body),
    )


def load_transcripts(directory: Path = config.TRANSCRIPTS_DIR) -> list[Transcript]:
    """All transcripts with transcript_status "ok" and at least one chunk, sorted by id."""
    transcripts = [load_transcript(p) for p in sorted(directory.glob("*.md"))]
    return [t for t in transcripts if t.transcript_status == "ok" and t.chunks]
