"""Blind human review of analyses: export files to fill in, then compare with the model (calibrate)."""

import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import get_args

from pydantic import BaseModel

from video_digest import config, db
from video_digest.schema import Verdict, VideoAnalysis
from video_digest.summarize import parse_analysis

BLIND_PATH = config.DATA_DIR / "review_blind.md"
MODEL_PATH = config.DATA_DIR / "review_model.md"

VERDICTS: tuple[str, ...] = get_args(Verdict)
VERDICT_RANK = {"skip": 0, "summary_enough": 1, "watch_full": 2}
SCORE_FIELDS = {"value": "practical_value", "density": "information_density", "demo": "demo_value"}
SHORT_NAMES = {v: k for k, v in SCORE_FIELDS.items()}
EMPTY_VERDICT = "my_verdict: ?"
EMPTY_SCORES = "my_scores: value=? density=? demo=?"

ENTRY_RE = re.compile(r"^## ", re.MULTILINE)
FIELD_RE = re.compile(r"^(video_id|prompt_version|my_verdict|my_scores):[ \t]*(.*)$", re.MULTILINE)
SCORE_RE = re.compile(r"\b(value|density|demo)\s*=\s*(\S+)")


class HumanRating(BaseModel):
    video_id: str
    prompt_version: str
    verdict: str
    scores: dict[str, int]  # keys: practical_value, information_density, demo_value


class Disagreement(BaseModel):
    video_id: str
    title: str
    my_verdict: str
    model_verdict: str
    score_diffs: dict[str, int]
    distance: float


class CalibrationReport(BaseModel):
    prompt_version: str
    model: str | None  # None: any model
    rated: int  # filled entries for this prompt version
    matched: int  # of those, with a stored analysis
    mae: dict[str, float | None]
    score_counts: dict[str, int]
    verdict_agreement: float | None  # percent
    confusion: dict[str, dict[str, int]]  # mine -> model -> count
    worst: list[Disagreement]


# --- export -----------------------------------------------------------------------


def _existing_lines(path: Path) -> dict[tuple[str, str], tuple[str, str]]:
    """Already-filled my_verdict / my_scores lines by (video_id, prompt_version), to keep on re-export."""
    if not path.exists():
        return {}
    kept: dict[tuple[str, str], tuple[str, str]] = {}
    for entry in ENTRY_RE.split(path.read_text(encoding="utf-8"))[1:]:
        fields = dict(FIELD_RE.findall(entry))
        if "video_id" in fields and "prompt_version" in fields:
            verdict_line = f"my_verdict: {fields.get('my_verdict', '?').strip() or '?'}"
            scores_line = f"my_scores: {fields.get('my_scores', '').strip()}" if "my_scores" in fields else EMPTY_SCORES
            kept[(fields["video_id"], fields["prompt_version"])] = (verdict_line, scores_line)
    return kept


class BlindItem(BaseModel):
    video: db.VideoRow
    summary: str  # in the display (digest) language
    key_points: list[str]  # "timestamp text", in the display language


Translator = Callable[[str, str, str], str]  # (text, target_language, source_language) -> text


def blind_item(
    video: db.VideoRow,
    row: db.AnalysisRow,
    analysis: VideoAnalysis,
    display_language: str,
    translator: Translator,
) -> BlindItem:
    """Summary and key points in display_language (translated when it differs from the analysis language)."""
    source = row.analysis_language or display_language
    key_points = "\n".join(f"- {kp.timestamp} {kp.text}" for kp in analysis.key_points)
    if source == display_language:
        summary, translated_points = analysis.summary, key_points
    else:
        summary = translator(analysis.summary, display_language, source)
        translated_points = translator(key_points, display_language, source)
    points = [line.removeprefix("- ").strip() for line in translated_points.splitlines() if line.strip()]
    return BlindItem(video=video, summary=summary, key_points=points)


def render_blind(
    items: list[BlindItem], prompt_version: str, keep: dict[tuple[str, str], tuple[str, str]], language: str
) -> str:
    lines = [
        f"# Blind review ({prompt_version}, shown in {language})",
        "",
        "Rate from the summary (and the video if you like) before looking at review_model.md.",
        f"my_verdict: one of {', '.join(VERDICTS)}. my_scores are optional, 0-10; leave '?' to skip one.",
        "Entries whose my_verdict is still '?' are ignored by `video-digest calibrate`.",
        "",
    ]
    for item in items:
        video = item.video
        verdict_line, scores_line = keep.get((video.id, prompt_version), (EMPTY_VERDICT, EMPTY_SCORES))
        lines += [
            f"## {video.title}",
            "",
            f"video_id: {video.id}",
            f"prompt_version: {prompt_version}",
            f"url: {video.url}",
            "",
            item.summary,
            "",
            *[f"- {point}" for point in item.key_points],
            "",
            verdict_line,
            scores_line,
            "",
        ]
    return "\n".join(lines)


def render_model(items: list[tuple[db.VideoRow, db.AnalysisRow, VideoAnalysis]], prompt_version: str) -> str:
    lines = [f"# Model review ({prompt_version})", ""]
    for video, row, a in items:
        s = a.scores
        lines += [
            f"## {video.title}",
            "",
            f"video_id: {video.id}",
            f"url: {video.url}",
            f"model: {row.model}; analysis_language: {row.analysis_language or '?'}",
            f"verdict: {a.verdict} ({a.verdict_reason})",
            f"scores: value={s.practical_value.score} density={s.information_density.score} demo={s.demo_value.score}",
            f"demo_confidence: {a.demo_confidence}; needs_screen: {a.needs_screen} ({a.needs_screen_evidence})",
            f"- practical_value: {s.practical_value.justification}",
            f"- information_density: {s.information_density.justification}",
            f"- demo_value: {s.demo_value.justification}",
            f"topics: {', '.join(t.slug for t in a.topics)}",
            "",
        ]
    return "\n".join(lines)


def export_review(
    conn: sqlite3.Connection,
    prompt_version: str,
    blind_path: Path = BLIND_PATH,
    model_path: Path = MODEL_PATH,
    *,
    display_language: str | None = None,
    translator: Translator | None = None,
) -> int:
    """Write both review files; ratings already filled in blind_path are preserved. Returns the entry count.

    The blind file shows summary and key points in display_language (default DIGEST_LANGUAGE);
    review_model.md stays in the analysis language.
    """
    language = display_language or config.DIGEST_LANGUAGE
    if translator is None:
        from video_digest.translate import translate

        def translate_with_cache(text: str, target: str, source: str) -> str:
            return translate(text, target, source, conn=conn)

        translator = translate_with_cache

    rows = db.latest_analyses(conn, prompt_version)
    parsed = [(video, row, parse_analysis(row)) for video, row in rows]
    items = [blind_item(video, row, a, language, translator) for video, row, a in parsed]
    keep = _existing_lines(blind_path)
    blind_path.parent.mkdir(parents=True, exist_ok=True)
    blind_path.write_text(render_blind(items, prompt_version, keep, language), encoding="utf-8")
    model_path.write_text(render_model(parsed, prompt_version), encoding="utf-8")
    return len(parsed)


# --- calibrate ----------------------------------------------------------------------


def parse_blind(text: str) -> list[HumanRating]:
    """Filled entries only: my_verdict must be a valid verdict; '?' scores are skipped."""
    ratings: list[HumanRating] = []
    for entry in ENTRY_RE.split(text)[1:]:
        fields = {k: v.strip() for k, v in FIELD_RE.findall(entry)}
        verdict = fields.get("my_verdict", "?")
        if verdict not in VERDICTS or "video_id" not in fields or "prompt_version" not in fields:
            continue
        scores: dict[str, int] = {}
        for short, value in SCORE_RE.findall(fields.get("my_scores", "")):
            if value.isdigit() and 0 <= int(value) <= 10:
                scores[SCORE_FIELDS[short]] = int(value)
        ratings.append(
            HumanRating(
                video_id=fields["video_id"], prompt_version=fields["prompt_version"], verdict=verdict, scores=scores
            )
        )
    return ratings


def calibrate(
    ratings: list[HumanRating],
    analyses: dict[str, tuple[db.VideoRow, VideoAnalysis]],
    prompt_version: str,
    model: str | None = None,
) -> CalibrationReport:
    """`analyses` must already be filtered by prompt version and model; `model` is only reported."""
    rated = [r for r in ratings if r.prompt_version == prompt_version]
    matched = [(r, *analyses[r.video_id]) for r in rated if r.video_id in analyses]

    diffs: dict[str, list[int]] = {name: [] for name in SCORE_FIELDS.values()}
    confusion = {mine: dict.fromkeys(VERDICTS, 0) for mine in VERDICTS}
    agree = 0
    disagreements: list[Disagreement] = []
    for rating, video, analysis in matched:
        confusion[rating.verdict][analysis.verdict] += 1
        agree += rating.verdict == analysis.verdict
        score_diffs: dict[str, int] = {}
        for name, mine in rating.scores.items():
            model_score = getattr(analysis.scores, name).score
            score_diffs[name] = model_score - mine
            diffs[name].append(abs(model_score - mine))
        verdict_gap = abs(VERDICT_RANK[rating.verdict] - VERDICT_RANK[analysis.verdict])
        disagreements.append(
            Disagreement(
                video_id=video.id,
                title=video.title,
                my_verdict=rating.verdict,
                model_verdict=analysis.verdict,
                score_diffs=score_diffs,
                distance=5 * verdict_gap + sum(abs(d) for d in score_diffs.values()),
            )
        )
    worst = sorted((d for d in disagreements if d.distance > 0), key=lambda d: -d.distance)[:5]
    return CalibrationReport(
        prompt_version=prompt_version,
        model=model,
        rated=len(rated),
        matched=len(matched),
        mae={k: round(sum(v) / len(v), 2) if v else None for k, v in diffs.items()},
        score_counts={k: len(v) for k, v in diffs.items()},
        verdict_agreement=round(100 * agree / len(matched), 1) if matched else None,
        confusion=confusion,
        worst=worst,
    )


def format_report(report: CalibrationReport) -> str:
    lines = [
        f"Compared: model={report.model or 'any'} prompt_version={report.prompt_version}",
        f"{report.matched} rated videos matched ({report.rated} rated).",
    ]
    if not report.matched:
        lines.append("Nothing to compare yet: fill my_verdict in review_blind.md first.")
        return "\n".join(lines)
    lines += ["", "Mean absolute difference (model vs you):"]
    for name, value in report.mae.items():
        shown = "-" if value is None else f"{value:.2f}"
        lines.append(f"  {name:<20} {shown:>5}  (n={report.score_counts[name]})")
    lines += ["", f"Verdict agreement: {report.verdict_agreement:.1f}%", "", "Confusion (rows: you, columns: model):"]
    width = max(len(v) for v in VERDICTS) + 2
    lines.append(" " * width + "".join(f"{v:>{width}}" for v in VERDICTS))
    for mine in VERDICTS:
        lines.append(f"{mine:<{width}}" + "".join(f"{report.confusion[mine][m]:>{width}}" for m in VERDICTS))
    lines += ["", "Largest disagreements:"]
    if not report.worst:
        lines.append("  none")
    for d in report.worst:
        diffs = " ".join(f"{SHORT_NAMES[k]}{v:+d}" for k, v in d.score_diffs.items())
        lines.append(f"  {d.video_id}  you={d.my_verdict} model={d.model_verdict}  {diffs}  {d.title[:60]}")
    return "\n".join(lines)


def load_analyses_for_calibration(
    conn: sqlite3.Connection, prompt_version: str, model: str | None = None
) -> dict[str, tuple[db.VideoRow, VideoAnalysis]]:
    rows = db.latest_analyses(conn, prompt_version, model)
    return {video.id: (video, parse_analysis(row)) for video, row in rows}
