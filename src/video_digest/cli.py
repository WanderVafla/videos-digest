"""Command-line entry point: `uv run video-digest <command>`."""

from pathlib import Path

import typer

from video_digest import config
from video_digest.ingest import DEFAULT_CHUNK_SECONDS, IngestResult, RateLimitedError, ingest_all, read_video_urls

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """video-digest: weekly digest of neural-network videos."""


@app.command()
def ingest(
    videos: Path = typer.Option(config.VIDEOS_FILE, help="File with one video URL per line."),
    chunk_seconds: int = typer.Option(DEFAULT_CHUNK_SECONDS, min=10, help="Length of a transcript chunk."),
    sleep: float | None = typer.Option(
        None, min=0, help="Pause between videos that need network requests (default: random 10-30 s)."
    ),
) -> None:
    """Fetch transcripts into data/transcripts/<video_id>.md (existing files are skipped)."""
    urls = read_video_urls(videos)
    if not urls:
        typer.echo(f"No URLs in {videos}")
        raise typer.Exit()

    def show(result: IngestResult) -> None:
        line = f"{result.status:<13} {result.video_id}"
        typer.echo(f"{line}  {result.detail}" if result.detail else line)

    try:
        results = ingest_all(urls, chunk_seconds=chunk_seconds, sleep_seconds=sleep, on_result=show)
    except RateLimitedError as exc:
        typer.echo(f"rate-limited  YouTube returned HTTP 429, stopping. Try again later. ({exc})", err=True)
        raise typer.Exit(2) from exc
    if any(r.status == "error" for r in results):
        raise typer.Exit(1)
