"""Command-line entry point: `uv run video-digest <command>`."""

from contextlib import closing
from pathlib import Path

import typer

from video_digest import config, db, review
from video_digest.ingest import (
    DEFAULT_CHUNK_SECONDS,
    IngestResult,
    RateLimitedError,
    ingest_all,
    read_video_urls,
)

app = typer.Typer(no_args_is_help=True, add_completion=False)

FATAL_HINTS = {
    401: "check LLM_API_KEY in .env (invalid or revoked key)",
    402: "check the provider balance (OpenRouter credits) or lower LLM_MAX_OUTPUT_TOKENS",
    403: f"check that the key has access to the model ({config.LLM_MODEL_FAST or 'LLM_MODEL_FAST'})",
}


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
    from video_digest.languages import SOURCE, is_valid_code
    from video_digest.summarize import PROMPT_VERSION, Outcome, RunStopped, summarize_all
    from video_digest.transcripts import Transcript, load_transcripts

    model = config.LLM_MODEL_FAST
    if not model or not config.LLM_API_KEY:
        typer.echo("Set LLM_API_KEY and LLM_MODEL_FAST in .env first.", err=True)
        raise typer.Exit(1)
    if config.ANALYSIS_LANGUAGE != SOURCE and not is_valid_code(config.ANALYSIS_LANGUAGE):
        typer.echo(f"ANALYSIS_LANGUAGE must be an ISO 639-1 code or 'source', got {config.ANALYSIS_LANGUAGE!r}", err=True)
        raise typer.Exit(1)
    transcripts = load_transcripts()
    if video_id and not any(t.video_id == video_id for t in transcripts):
        typer.echo(f"No transcript with status ok for {video_id}", err=True)
        raise typer.Exit(1)
    typer.echo(
        f"model={model} prompt={PROMPT_VERSION} language={config.ANALYSIS_LANGUAGE} transcripts={len(transcripts)}"
    )

    def start(i: int, total: int, t: Transcript) -> None:
        typer.echo(f"[{i}/{total}] {t.video_id} {t.title[:60]} ... ", nl=False)

    def show(o: Outcome) -> None:
        if o.status == "error":
            typer.echo(f"ERROR {o.detail}")
            return
        cost = f" ${o.cost_usd:.4f}" if o.cost_usd is not None else ""
        topics_note = f"  [{o.detail}]" if o.detail else ""
        typer.echo(f"{o.verdict}  in={o.tokens_in} out={o.tokens_out}{cost}{topics_note}")

    try:
        with closing(db.connect()) as conn:
            outcomes = summarize_all(
                conn, transcripts, model, force=force, limit=limit, only_id=video_id, on_start=start, on_result=show
            )
    except RunStopped as stop:
        done = [o for o in stop.outcomes if o.status == "ok"]
        typer.echo("STOPPED")
        typer.echo(
            f"stopped on {stop.video_id}: HTTP {stop.status} from the provider; "
            f"{len(done)} done and saved, {len(stop.outcomes) - len(done)} failed before it.",
            err=True,
        )
        typer.echo(f"hint: {FATAL_HINTS.get(stop.status or 0, 'check the API key, balance and model access')}", err=True)
        raise typer.Exit(2) from stop
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
def export_review(
    model: str | None = typer.Option(None, help="Export analyses of this model (default: LLM_MODEL_FAST)."),
) -> None:
    """Write data/review_blind.md (to fill in, in DIGEST_LANGUAGE) and data/review_model.md (model scores)."""
    from video_digest.languages import is_valid_code
    from video_digest.summarize import PROMPT_VERSION

    if not is_valid_code(config.DIGEST_LANGUAGE):
        typer.echo(f"DIGEST_LANGUAGE must be an ISO 639-1 code, got {config.DIGEST_LANGUAGE!r}", err=True)
        raise typer.Exit(1)
    model = model or config.LLM_MODEL_FAST or None
    typer.echo(f"Exporting: model={model or 'any'} prompt_version={PROMPT_VERSION}")
    try:
        with closing(db.connect()) as conn:
            count = review.export_review(conn, PROMPT_VERSION, model=model)
    except Exception as exc:  # translation calls can fail; finished translations stay cached
        typer.echo(f"export-review failed: {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"{count} videos -> {review.BLIND_PATH} (fill in) and {review.MODEL_PATH} (model)")


@app.command()
def calibrate(
    model: str | None = typer.Option(None, help="Compare analyses of this model (default: LLM_MODEL_FAST)."),
) -> None:
    """Compare your ratings in data/review_blind.md with the model's analyses."""
    from video_digest.summarize import PROMPT_VERSION

    if not review.BLIND_PATH.exists():
        typer.echo(f"{review.BLIND_PATH} not found; run `video-digest export-review` first.", err=True)
        raise typer.Exit(1)
    model = model or config.LLM_MODEL_FAST or None
    ratings = review.parse_blind(review.BLIND_PATH.read_text(encoding="utf-8"))
    with closing(db.connect()) as conn:
        analyses = review.load_analyses_for_calibration(conn, PROMPT_VERSION, model)
    typer.echo(review.format_report(review.calibrate(ratings, analyses, PROMPT_VERSION, model)))


@app.command()
def prune(
    prompt_version: str = typer.Option(..., "--prompt-version", help="Prompt version whose analyses to delete."),
    yes: bool = typer.Option(False, "--yes", help="Really delete (default: dry run, only list)."),
) -> None:
    """Delete all analyses of an old prompt version (dry run unless --yes)."""
    from video_digest.summarize import PROMPT_VERSION

    if prompt_version == PROMPT_VERSION:
        typer.echo(f"Refusing to prune the current prompt version {PROMPT_VERSION}.", err=True)
        raise typer.Exit(1)
    with closing(db.connect()) as conn:
        summary = db.version_summary(conn, prompt_version)
        if not summary.count:
            typer.echo(f"No analyses with prompt_version={prompt_version}.")
            return
        models = ", ".join(f"{m} ({n})" for m, n in summary.models.items())
        typer.echo(f"{summary.count} analyses of {prompt_version}; models: {models}; total cost ${summary.cost_usd:.4f}")
        if not yes:
            typer.echo("Dry run: nothing deleted. Re-run with --yes to delete them.")
            return
        deleted = db.delete_analyses(conn, prompt_version)
    typer.echo(f"Deleted {deleted} analyses of {prompt_version}.")


topics_app = typer.Typer(help="Topic taxonomy in topics.txt.")
app.add_typer(topics_app, name="topics")


@topics_app.callback(invoke_without_command=True)
def topics_list(ctx: typer.Context) -> None:
    """List topic slugs with usage counts (newest analysis per video, current prompt version)."""
    if ctx.invoked_subcommand is not None:
        return
    from video_digest import topics
    from video_digest.summarize import PROMPT_VERSION

    known = topics.load_topics(config.TOPICS_FILE)
    with closing(db.connect()) as conn:
        usage = topics.stored_usage(conn, PROMPT_VERSION)
    if not known and not usage:
        typer.echo(f"No topics yet ({config.TOPICS_FILE}).")
        return
    for slug in sorted(set(known) | set(usage), key=lambda s: (-usage[s], s)):
        marker = "" if slug in known else "  (not in topics.txt)"
        typer.echo(f"{usage[slug]:>4}  {slug}: {known.get(slug, '')}{marker}")


@topics_app.command("merge")
def topics_merge(old: str, new: str) -> None:
    """Rewrite OLD to NEW in stored analyses (current prompt version) and topics.txt, and remove OLD."""
    from video_digest import topics
    from video_digest.summarize import PROMPT_VERSION

    if old == new:
        typer.echo("OLD and NEW are the same.", err=True)
        raise typer.Exit(1)
    try:
        with closing(db.connect()) as conn:
            changed = topics.merge(conn, config.TOPICS_FILE, old, new, PROMPT_VERSION)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Merged {old} -> {new}: {changed} {PROMPT_VERSION} analyses updated, topics.txt rewritten.")
