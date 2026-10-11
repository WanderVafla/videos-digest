"""Command-line entry point: `uv run video-digest <command>`."""

from contextlib import closing
from pathlib import Path

import typer

from video_digest import config, db, review
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


@app.command()
def summarize(
    limit: int | None = typer.Option(None, min=1, help="Analyze at most N videos that need an API call."),
    force: bool = typer.Option(False, "--force", help="Re-run and overwrite cached analyses."),
    video_id: str | None = typer.Option(None, "--id", help="Only this video id."),
) -> None:
    """Summarize and score transcripts with LLM_MODEL_FAST (cached by transcript hash)."""
    from video_digest.summarize import PROMPT_VERSION, Outcome, summarize_all
    from video_digest.transcripts import Transcript, load_transcripts

    model = config.LLM_MODEL_FAST
    if not model or not config.LLM_API_KEY:
        typer.echo("Set LLM_API_KEY and LLM_MODEL_FAST in .env first.", err=True)
        raise typer.Exit(1)
    transcripts = load_transcripts()
    if video_id and not any(t.video_id == video_id for t in transcripts):
        typer.echo(f"No transcript with status ok for {video_id}", err=True)
        raise typer.Exit(1)
    typer.echo(f"model={model} prompt={PROMPT_VERSION} transcripts={len(transcripts)}")

    def start(i: int, total: int, t: Transcript) -> None:
        typer.echo(f"[{i}/{total}] {t.video_id} {t.title[:60]} ... ", nl=False)

    def show(o: Outcome) -> None:
        if o.status == "error":
            typer.echo(f"ERROR {o.detail}")
            return
        cost = f" ${o.cost_usd:.4f}" if o.cost_usd is not None else ""
        typer.echo(f"{o.verdict}  in={o.tokens_in} out={o.tokens_out}{cost}")

    with closing(db.connect()) as conn:
        outcomes = summarize_all(
            conn, transcripts, model, force=force, limit=limit, only_id=video_id, on_start=start, on_result=show
        )
    if not outcomes:
        typer.echo("Nothing to do: all transcripts already have an analysis (use --force to redo).")
        return
    ok = [o for o in outcomes if o.status == "ok"]
    costs = [o.cost_usd for o in ok if o.cost_usd is not None]
    total_cost = f", cost ${sum(costs):.4f}" if costs else ""
    typer.echo(
        f"done: {len(ok)} ok, {len(outcomes) - len(ok)} failed; "
        f"tokens in={sum(o.tokens_in for o in ok)} out={sum(o.tokens_out for o in ok)}{total_cost}"
    )
    if len(ok) < len(outcomes):
        raise typer.Exit(1)


@app.command("export-review")
def export_review() -> None:
    """Write data/review_blind.md (to fill in) and data/review_model.md (model scores)."""
    from video_digest.summarize import PROMPT_VERSION

    with closing(db.connect()) as conn:
        count = review.export_review(conn, PROMPT_VERSION)
    typer.echo(f"{count} videos -> {review.BLIND_PATH} (fill in) and {review.MODEL_PATH} (model)")


@app.command()
def calibrate() -> None:
    """Compare your ratings in data/review_blind.md with the model's analyses."""
    from video_digest.summarize import PROMPT_VERSION

    if not review.BLIND_PATH.exists():
        typer.echo(f"{review.BLIND_PATH} not found; run `video-digest export-review` first.", err=True)
        raise typer.Exit(1)
    ratings = review.parse_blind(review.BLIND_PATH.read_text(encoding="utf-8"))
    with closing(db.connect()) as conn:
        analyses = review.load_analyses_for_calibration(conn, PROMPT_VERSION)
    typer.echo(review.format_report(review.calibrate(ratings, analyses, PROMPT_VERSION)))
