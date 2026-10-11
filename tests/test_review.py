"""export-review and calibrate: blind file format, parsing, comparison and report. No network."""

import copy
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from video_digest import db
from video_digest.review import (
    HumanRating,
    calibrate,
    export_review,
    format_report,
    load_analyses_for_calibration,
    parse_blind,
)
from video_digest.schema import VideoAnalysis

PV = "summarize_v1"


def analysis(data: dict[str, Any], verdict: str, value: int, density: int, demo: int) -> VideoAnalysis:
    d = copy.deepcopy(data)
    d["verdict"] = verdict
    d["scores"]["practical_value"]["score"] = value
    d["scores"]["information_density"]["score"] = density
    d["scores"]["demo_value"]["score"] = demo
    return VideoAnalysis.model_validate(d)


def store(conn: sqlite3.Connection, vid: str, a: VideoAnalysis, model: str = "m", prompt_version: str = PV) -> None:
    db.upsert_video(
        conn,
        db.VideoRow(
            id=vid, title=f"Title {vid}", channel="c", url=f"https://youtu.be/{vid}", upload_date="2026-05-01",
            duration_seconds=600, source_language="en", transcript_path=f"{vid}.md",
        ),
    )
    db.save_analysis(
        conn,
        db.AnalysisRow(
            video_id=vid, prompt_version=prompt_version, model=model, cache_key=f"{vid}-{model}-{prompt_version}",
            analysis_json=a.model_dump_json(), hints_json="{}",
        ),
    )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return db.connect(tmp_path / "db.sqlite")


def test_export_writes_blind_and_model_files(tmp_path: Path, conn: sqlite3.Connection, analysis_data: dict[str, Any]) -> None:
    store(conn, "aaaaaaaaaaa", analysis(analysis_data, "watch_full", 8, 7, 9))
    store(conn, "bbbbbbbbbbb", analysis(analysis_data, "skip", 1, 2, 0))
    store(conn, "ccccccccccc", analysis(analysis_data, "skip", 1, 2, 0), prompt_version="old_v0")
    blind, model = tmp_path / "review_blind.md", tmp_path / "review_model.md"

    assert export_review(conn, PV, blind, model) == 2
    blind_text = blind.read_text(encoding="utf-8")
    assert blind_text.count("my_verdict: ?") == 2
    assert blind_text.count("my_scores: value=? density=? demo=?") == 2
    assert "video_id: aaaaaaaaaaa" in blind_text and f"prompt_version: {PV}" in blind_text
    assert analysis_data["summary_ru"] in blind_text
    assert "https://youtu.be/aaaaaaaaaaa" in blind_text
    # blind: no model scores or verdicts
    for leak in ["watch_full (", "verdict: watch_full", "value=8", "demo_confidence", "Concrete install steps"]:
        assert leak not in blind_text
    assert "ccccccccccc" not in blind_text  # other prompt version

    model_text = model.read_text(encoding="utf-8")
    assert "verdict: watch_full" in model_text and "value=8 density=7 demo=9" in model_text
    assert "demo_confidence: high" in model_text


def test_re_export_keeps_filled_ratings(tmp_path: Path, conn: sqlite3.Connection, analysis_data: dict[str, Any]) -> None:
    store(conn, "aaaaaaaaaaa", analysis(analysis_data, "watch_full", 8, 7, 9))
    blind, model = tmp_path / "review_blind.md", tmp_path / "review_model.md"
    export_review(conn, PV, blind, model)
    blind.write_text(
        blind.read_text(encoding="utf-8")
        .replace("my_verdict: ?", "my_verdict: skip")
        .replace("value=? density=? demo=?", "value=3 density=? demo=1"),
        encoding="utf-8",
    )
    store(conn, "bbbbbbbbbbb", analysis(analysis_data, "skip", 1, 2, 0))
    export_review(conn, PV, blind, model)
    text = blind.read_text(encoding="utf-8")
    assert "my_verdict: skip" in text and "my_scores: value=3 density=? demo=1" in text
    assert text.count("my_verdict: ?") == 1  # the new video


BLIND = """# Blind review (summarize_v1)

## First video

video_id: aaaaaaaaaaa
prompt_version: summarize_v1
url: https://youtu.be/aaaaaaaaaaa

Резюме. Тут есть my_verdict: watch_full внутри текста, но не в начале строки.

my_verdict: summary_enough
my_scores: value=6 density=? demo=2

## Second video (unfilled)

video_id: bbbbbbbbbbb
prompt_version: summarize_v1

my_verdict: ?
my_scores: value=? density=? demo=?

## Third video

video_id: ccccccccccc
prompt_version: summarize_v1

my_verdict: watch_full
my_scores: value=? density=? demo=?

## Bad verdict

video_id: ddddddddddd
prompt_version: summarize_v1

my_verdict: maybe
my_scores: value=11 density=5 demo=5

## Old prompt

video_id: eeeeeeeeeee
prompt_version: old_v0

my_verdict: skip
"""


def test_parse_blind() -> None:
    ratings = parse_blind(BLIND)
    assert [(r.video_id, r.verdict, r.scores) for r in ratings] == [
        ("aaaaaaaaaaa", "summary_enough", {"practical_value": 6, "demo_value": 2}),
        ("ccccccccccc", "watch_full", {}),
        ("eeeeeeeeeee", "skip", {}),
    ]
    assert ratings[2].prompt_version == "old_v0"


def test_calibrate_report(analysis_data: dict[str, Any]) -> None:
    def video(vid: str) -> db.VideoRow:
        return db.VideoRow(id=vid, title=f"Title {vid}", channel=None, url="u", upload_date=None,
                           duration_seconds=None, source_language="en", transcript_path="p")  # fmt: skip

    analyses = {
        "aaaaaaaaaaa": (video("aaaaaaaaaaa"), analysis(analysis_data, "summary_enough", 8, 5, 5)),
        "bbbbbbbbbbb": (video("bbbbbbbbbbb"), analysis(analysis_data, "skip", 1, 1, 0)),
        "ccccccccccc": (video("ccccccccccc"), analysis(analysis_data, "skip", 2, 2, 1)),
    }
    ratings = [
        HumanRating(video_id="aaaaaaaaaaa", prompt_version=PV, verdict="summary_enough", scores={"practical_value": 6, "demo_value": 2}),
        HumanRating(video_id="bbbbbbbbbbb", prompt_version=PV, verdict="skip", scores={"practical_value": 1}),
        HumanRating(video_id="ccccccccccc", prompt_version=PV, verdict="watch_full", scores={}),
        HumanRating(video_id="zzzzzzzzzzz", prompt_version=PV, verdict="skip", scores={}),  # no analysis
        HumanRating(video_id="aaaaaaaaaaa", prompt_version="old_v0", verdict="skip", scores={}),  # other version
    ]
    report = calibrate(ratings, analyses, PV)
    assert (report.rated, report.matched) == (4, 3)
    assert report.mae == {"practical_value": 1.0, "information_density": None, "demo_value": 3.0}
    assert report.score_counts == {"practical_value": 2, "information_density": 0, "demo_value": 1}
    assert report.verdict_agreement == pytest.approx(66.7)
    assert report.confusion["watch_full"]["skip"] == 1
    assert report.confusion["summary_enough"]["summary_enough"] == 1
    assert [d.video_id for d in report.worst] == ["ccccccccccc", "aaaaaaaaaaa"]  # verdict gap 2 -> 10; scores 2+3 -> 5

    text = format_report(report)
    assert "3 rated videos matched (4 rated)" in text
    assert "Verdict agreement: 66.7%" in text
    assert "practical_value       1.00  (n=2)" in text
    assert "information_density      -  (n=0)" in text
    assert "ccccccccccc  you=watch_full model=skip" in text
    assert "aaaaaaaaaaa  you=summary_enough model=summary_enough  value+2 demo+3" in text
    confusion_lines = text.split("Confusion (rows: you, columns: model):\n")[1].splitlines()
    assert confusion_lines[0].split() == ["watch_full", "summary_enough", "skip"]
    assert confusion_lines[1].split() == ["watch_full", "0", "0", "1"]


def test_calibrate_end_to_end_from_db(tmp_path: Path, conn: sqlite3.Connection, analysis_data: dict[str, Any]) -> None:
    store(conn, "aaaaaaaaaaa", analysis(analysis_data, "watch_full", 8, 7, 9))
    blind, model = tmp_path / "review_blind.md", tmp_path / "review_model.md"
    export_review(conn, PV, blind, model)
    report = calibrate(parse_blind(blind.read_text(encoding="utf-8")), load_analyses_for_calibration(conn, PV), PV)
    assert report.matched == 0
    assert "Nothing to compare yet" in format_report(report)

    blind.write_text(blind.read_text(encoding="utf-8").replace("my_verdict: ?", "my_verdict: watch_full"), encoding="utf-8")
    report = calibrate(parse_blind(blind.read_text(encoding="utf-8")), load_analyses_for_calibration(conn, PV), PV)
    assert (report.matched, report.verdict_agreement, report.worst) == (1, 100.0, [])


def test_latest_analysis_per_video(conn: sqlite3.Connection, analysis_data: dict[str, Any]) -> None:
    store(conn, "aaaaaaaaaaa", analysis(analysis_data, "skip", 1, 1, 1), model="m1")
    store(conn, "aaaaaaaaaaa", analysis(analysis_data, "watch_full", 9, 9, 9), model="m2")
    rows = db.latest_analyses(conn, PV)
    assert len(rows) == 1 and rows[0][1].model == "m2"
