"""Stage 1: fetch transcripts for videos.txt and save them as chunked Markdown.

Network rules (YouTube rate-limits aggressively):
- never request translated auto-captions, only the video's own track;
- one subtitle track per video, fetched with a single request;
- videos whose data/transcripts/<id>.md exists are skipped without any request.
"""

import json
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, Self, cast
from urllib.parse import parse_qs, urlparse

import yt_dlp
from pydantic import BaseModel

from video_digest import config

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
DEFAULT_CHUNK_SECONDS = 120
DEFAULT_SLEEP_RANGE = (10.0, 30.0)  # random pause between network fetches, seconds
DOUBLE_DASH_RE = re.compile(r"\{dash\}\s*\{dash\}")

Status = Literal["ok", "no_transcript", "cached", "error"]


class Track(BaseModel):
    lang: str
    manual: bool


class VideoMeta(BaseModel):
    id: str
    title: str
    channel: str | None
    upload_date: str | None  # YYYY-MM-DD
    duration: int | None  # seconds
    language: str | None
    chapters: list[dict[str, Any]]
    webpage_url: str
    subtitles: list[str]


class Segment(BaseModel):
    start_ms: int
    text: str


class Chunk(BaseModel):
    start_ms: int
    end_ms: int
    text: str


class IngestResult(BaseModel):
    video_id: str
    status: Status
    detail: str = ""


class RateLimitedError(Exception):
    """YouTube answered HTTP 429; the whole run must stop."""


class Ydl(Protocol):
    """The subset of yt_dlp.YoutubeDL used here (lets tests pass a fake)."""

    def __enter__(self) -> Self: ...
    def __exit__(self, *args: object) -> object: ...
    def extract_info(self, url: str, download: bool = ...) -> Any: ...
    def urlopen(self, req: str) -> Any: ...


YdlFactory = Callable[[dict[str, Any]], Ydl]


# --- input ---------------------------------------------------------------


def video_id_from_url(url: str) -> str | None:
    """Extract the id from youtube.com/watch?v=, youtu.be/ and /shorts/ URLs."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").removeprefix("www.").removeprefix("m.")
    candidate: str | None = None
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host.endswith("youtube.com"):
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.startswith("/shorts/"):
            candidate = parsed.path.split("/")[2]
    if candidate and VIDEO_ID_RE.match(candidate):
        return candidate
    return None


def read_video_urls(path: Path) -> list[str]:
    """One URL per line; empty lines and lines starting with # are ignored."""
    if not path.exists():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


# --- yt-dlp ----------------------------------------------------------------


def make_ydl(opts: dict[str, Any]) -> yt_dlp.YoutubeDL:
    """The only place a YoutubeDL is constructed."""
    return yt_dlp.YoutubeDL(cast("Any", opts))


def ydl_opts() -> dict[str, Any]:
    opts: dict[str, Any] = {"skip_download": True, "quiet": True, "no_warnings": True}
    if config.YTDLP_JS_RUNTIME:
        opts["js_runtimes"] = {config.YTDLP_JS_RUNTIME: {}}
    return opts


def is_rate_limited(exc: BaseException) -> bool:
    if getattr(exc, "status", None) == 429:
        return True
    inner = getattr(exc, "exc_info", None)
    if inner and isinstance(inner[1], BaseException) and inner[1] is not exc:
        return is_rate_limited(inner[1])
    text = str(exc)
    return "HTTP Error 429" in text or "Too Many Requests" in text


# --- pure logic ------------------------------------------------------------


def _iso_date(value: str | None) -> str | None:
    if value and len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def metadata_from_info(info: dict[str, Any]) -> VideoMeta:
    return VideoMeta(
        id=info["id"],
        title=info.get("title") or "",
        channel=info.get("channel") or info.get("uploader"),
        upload_date=_iso_date(info.get("upload_date")),
        duration=int(info["duration"]) if info.get("duration") else None,
        language=info.get("language"),
        chapters=info.get("chapters") or [],
        webpage_url=info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}",
        subtitles=list(info.get("subtitles") or {}),
    )


def _same_language(track_lang: str, language: str) -> bool:
    """"en-US-orig" matches "en", "en-US" etc. by base language code."""
    base = track_lang.removesuffix("-orig").split("-")[0].lower()
    return base == language.split("-")[0].lower()


def foreign_orig_tracks(info: dict[str, Any]) -> list[str]:
    """"-orig" auto tracks in a language other than the video's (dubbed audio tracks)."""
    language = info.get("language")
    auto: dict[str, Any] = info.get("automatic_captions") or {}
    return [k for k in auto if k.endswith("-orig") and language and not _same_language(k, language)]


def pick_track(info: dict[str, Any]) -> Track | None:
    """Prefer the video's original language. Order when info["language"] is known:
    manual subs in that language, its "-orig" auto track, any manual subs, else None.

    "-orig" tracks in other languages come from auto-dubbed audio and are never chosen.
    If the language is unknown: first manual subs, else the first "-orig" auto track.
    Translated auto-captions are never chosen: YouTube answers them with 429.
    """
    language: str | None = info.get("language")
    manual: dict[str, Any] = info.get("subtitles") or {}
    auto: dict[str, Any] = info.get("automatic_captions") or {}
    orig = [lang for lang in auto if lang.endswith("-orig")]
    if language:
        for lang in manual:
            if _same_language(lang, language):
                return Track(lang=lang, manual=True)
        for lang in orig:
            if _same_language(lang, language):
                return Track(lang=lang, manual=False)
    if manual:
        return Track(lang=next(iter(manual)), manual=True)
    if orig and not language:
        return Track(lang=orig[0], manual=False)
    return None


def subtitle_url(info: dict[str, Any], track: Track) -> str | None:
    """URL of the json3 variant of the chosen track."""
    source = info.get("subtitles" if track.manual else "automatic_captions") or {}
    formats: list[dict[str, Any]] = source.get(track.lang) or []
    return next((f["url"] for f in formats if f.get("ext") == "json3" and f.get("url")), None)


def normalize_caption_text(text: str) -> str:
    """Collapse whitespace and fix YouTube's "{dash}" artifact: "{dash} {dash}" -> "--", "{dash}" -> "-"."""
    text = DOUBLE_DASH_RE.sub("--", text).replace("{dash}", "-")
    return " ".join(text.split())


def parse_json3(data: dict[str, Any]) -> list[Segment]:
    """YouTube json3 -> text segments. Events without segs or with only whitespace are dropped."""
    segments: list[Segment] = []
    for event in data.get("events", []):
        segs = event.get("segs")
        if not segs:
            continue
        text = normalize_caption_text("".join(s.get("utf8", "") for s in segs))
        if text:
            segments.append(Segment(start_ms=int(event.get("tStartMs", 0)), text=text))
    return segments


def chunk_segments(segments: list[Segment], chunk_seconds: int = DEFAULT_CHUNK_SECONDS) -> list[Chunk]:
    """Group segments into fixed time windows [k*size, (k+1)*size); empty windows are skipped."""
    size_ms = chunk_seconds * 1000
    groups: dict[int, list[str]] = {}
    for seg in segments:
        groups.setdefault(seg.start_ms // size_ms, []).append(seg.text)
    return [
        Chunk(start_ms=k * size_ms, end_ms=(k + 1) * size_ms, text=" ".join(texts))
        for k, texts in sorted(groups.items())
    ]


def format_ts(ms: int) -> str:
    seconds = ms // 1000
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def render_markdown(
    meta: VideoMeta,
    track: Track | None,
    chunks: list[Chunk],
    chunk_seconds: int,
    ingested_at: datetime,
    foreign_tracks: list[str] | None = None,
) -> str:
    """Frontmatter values are JSON-encoded, which is valid YAML."""
    front: dict[str, Any] = {
        "video_id": meta.id,
        "title": meta.title,
        "channel": meta.channel,
        "url": meta.webpage_url,
        "upload_date": meta.upload_date,
        "duration": meta.duration,
        "language": meta.language,
        "transcript_status": "ok" if track else "no_transcript",
        "transcript_lang": track.lang if track else None,
        "transcript_manual": track.manual if track else None,
        "chunk_seconds": chunk_seconds,
        "chunks": len(chunks),
        "chapters": [
            {"title": c.get("title"), "start": c.get("start_time"), "end": c.get("end_time")}
            for c in meta.chapters
        ],
        "subtitles": meta.subtitles,
        "ingested_at": ingested_at.isoformat(timespec="seconds"),
    }
    lines = ["---"]
    lines += [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in front.items()]
    lines += ["---", "", f"# {meta.title}", ""]
    if not track:
        lines += ["_No transcript available (no manual subtitles and no original-language auto track)._", ""]
        if foreign_tracks:
            lines += [f"_Skipped auto tracks in other languages (dubbed audio): {', '.join(foreign_tracks)}._", ""]
    for chunk in chunks:
        lines += [f"## [{format_ts(chunk.start_ms)} - {format_ts(chunk.end_ms)}]", "", chunk.text, ""]
    return "\n".join(lines)


# --- orchestration -----------------------------------------------------------


def ingest_video(
    url: str,
    ydl: Ydl,
    transcripts_dir: Path,
    raw_dir: Path,
    chunk_seconds: int,
) -> IngestResult:
    """Metadata request + at most one subtitle request. Writes <id>.md on success or when
    the video has no transcript; raises RateLimitedError on HTTP 429."""
    try:
        info = cast("dict[str, Any]", ydl.extract_info(url, download=False))
        meta = metadata_from_info(info)
        track = pick_track(info)
        chunks: list[Chunk] = []
        if track:
            sub_url = subtitle_url(info, track)
            if sub_url is None:
                return IngestResult(video_id=meta.id, status="error", detail=f"no json3 format for {track.lang}")
            raw = ydl.urlopen(sub_url).read()
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"{meta.id}.{track.lang}.json3").write_bytes(raw)
            chunks = chunk_segments(parse_json3(json.loads(raw)), chunk_seconds)
    except Exception as exc:
        if is_rate_limited(exc):
            raise RateLimitedError(str(exc)) from exc
        return IngestResult(video_id=video_id_from_url(url) or url, status="error", detail=str(exc))

    transcripts_dir.mkdir(parents=True, exist_ok=True)
    foreign = foreign_orig_tracks(info)
    markdown = render_markdown(meta, track, chunks, chunk_seconds, datetime.now(UTC), foreign)
    (transcripts_dir / f"{meta.id}.md").write_text(markdown, encoding="utf-8")
    if track:
        return IngestResult(video_id=meta.id, status="ok", detail=f"{track.lang}, {len(chunks)} chunks")
    detail = f"only dubbed tracks: {', '.join(foreign)}" if foreign else ""
    return IngestResult(video_id=meta.id, status="no_transcript", detail=detail)


def ingest_all(
    urls: list[str],
    transcripts_dir: Path = config.TRANSCRIPTS_DIR,
    raw_dir: Path = config.RAW_DIR,
    chunk_seconds: int = DEFAULT_CHUNK_SECONDS,
    sleep_seconds: float | None = None,
    ydl_factory: YdlFactory = make_ydl,
    on_result: Callable[[IngestResult], None] | None = None,
) -> list[IngestResult]:
    """Ingest every URL; stops at the first HTTP 429 (RateLimitedError propagates).

    Between videos that need network requests it pauses sleep_seconds, or a random
    DEFAULT_SLEEP_RANGE delay when None. Cached videos never cause a pause.
    """
    results: list[IngestResult] = []

    def report(result: IngestResult) -> None:
        results.append(result)
        if on_result:
            on_result(result)

    pending: list[str] = []
    seen: set[str] = set()
    for url in urls:
        video_id = video_id_from_url(url)
        if video_id in seen:
            continue
        if video_id is None:
            report(IngestResult(video_id=url, status="error", detail="not a YouTube video URL"))
        elif (transcripts_dir / f"{video_id}.md").exists():
            seen.add(video_id)
            report(IngestResult(video_id=video_id, status="cached"))
        else:
            seen.add(video_id)
            pending.append(f"https://www.youtube.com/watch?v={video_id}")

    if not pending:
        return results
    with ydl_factory(ydl_opts()) as ydl:
        for i, url in enumerate(pending):
            if i:
                delay = random.uniform(*DEFAULT_SLEEP_RANGE) if sleep_seconds is None else sleep_seconds
                if delay:
                    time.sleep(delay)
            report(ingest_video(url, ydl, transcripts_dir, raw_dir, chunk_seconds))
    return results
