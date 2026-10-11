"""Stage 1 tests. Fixtures and fakes only: no network calls."""

import io
import json
from pathlib import Path
from typing import Any, Self

import pytest

from video_digest.ingest import (
    RateLimitedError,
    Segment,
    Track,
    chunk_segments,
    foreign_orig_tracks,
    format_ts,
    ingest_all,
    is_rate_limited,
    metadata_from_info,
    normalize_caption_text,
    parse_json3,
    pick_track,
    read_video_urls,
    subtitle_url,
    video_id_from_url,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample.ru.json3"
VID = "aBcW01Qbuws"


def fixture_json3() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --- input ---------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"https://youtube.com/watch?v={VID}&t=42s",
        f"https://m.youtube.com/watch?list=PL1&v={VID}",
        f"https://youtu.be/{VID}",
        f"https://youtu.be/{VID}?si=abc",
        f"https://www.youtube.com/shorts/{VID}",
        f"  https://www.youtube.com/watch?v={VID}  ",
    ],
)
def test_video_id_from_url(url: str) -> None:
    assert video_id_from_url(url) == VID


@pytest.mark.parametrize(
    "url",
    ["https://example.com/watch?v=aBcW01Qbuws", "https://www.youtube.com/watch?v=short", "not a url", ""],
)
def test_video_id_from_url_rejects(url: str) -> None:
    assert video_id_from_url(url) is None


def test_read_video_urls(tmp_path: Path) -> None:
    path = tmp_path / "videos.txt"
    path.write_text("# comment\n\nhttps://youtu.be/aBcW01Qbuws\n  # indented comment\n https://youtu.be/x \n")
    assert read_video_urls(path) == ["https://youtu.be/aBcW01Qbuws", "https://youtu.be/x"]
    assert read_video_urls(tmp_path / "missing.txt") == []


# --- track selection ---------------------------------------------------------


def test_pick_track_manual_prefers_video_language() -> None:
    info = {"language": "ru", "subtitles": {"en": [], "ru": []}, "automatic_captions": {"ru-orig": []}}
    assert pick_track(info) == Track(lang="ru", manual=True)


def test_pick_track_manual_matches_regional_variant() -> None:
    info = {"language": "en", "subtitles": {"de": [], "en-US": []}}
    assert pick_track(info) == Track(lang="en-US", manual=True)


def test_pick_track_manual_falls_back_to_first() -> None:
    info = {"language": "ru", "subtitles": {"de": [], "en": []}}
    assert pick_track(info) == Track(lang="de", manual=True)


def test_pick_track_auto_orig_only() -> None:
    info = {"language": "ru", "subtitles": {}, "automatic_captions": {"en": [], "de": [], "ru-orig": [], "ru": []}}
    assert pick_track(info) == Track(lang="ru-orig", manual=False)


def test_pick_track_prefers_original_over_dub_listed_first() -> None:
    # Russian video with an English auto-dub: YouTube lists the dub's "-orig" first
    info = {"language": "ru", "subtitles": {}, "automatic_captions": {"en": [], "en-US-orig": [], "ru-orig": []}}
    assert pick_track(info) == Track(lang="ru-orig", manual=False)


def test_pick_track_original_auto_beats_foreign_manual() -> None:
    info = {"language": "ru", "subtitles": {"en": []}, "automatic_captions": {"ru-orig": []}}
    assert pick_track(info) == Track(lang="ru-orig", manual=False)


def test_pick_track_matches_regional_orig() -> None:
    info = {"language": "en", "automatic_captions": {"de-orig": [], "en-US-orig": []}}
    assert pick_track(info) == Track(lang="en-US-orig", manual=False)


def test_pick_track_skips_dub_only() -> None:
    # English video whose only "-orig" track comes from an Arabic dub
    info = {"language": "en-US", "subtitles": {}, "automatic_captions": {"ar-orig": [], "ar": [], "en": []}}
    assert pick_track(info) is None
    assert foreign_orig_tracks(info) == ["ar-orig"]


def test_pick_track_unknown_language_takes_first_orig() -> None:
    info = {"language": None, "automatic_captions": {"en": [], "de-orig": []}}
    assert pick_track(info) == Track(lang="de-orig", manual=False)


def test_pick_track_never_picks_translated_auto() -> None:
    info = {"language": "ru", "subtitles": {}, "automatic_captions": {"en": [], "ru": []}}
    assert pick_track(info) is None
    assert pick_track({}) is None


def test_subtitle_url_picks_json3() -> None:
    info = {
        "automatic_captions": {
            "ru-orig": [{"ext": "vtt", "url": "u-vtt"}, {"ext": "json3", "url": "u-json3"}],
        }
    }
    assert subtitle_url(info, Track(lang="ru-orig", manual=False)) == "u-json3"
    assert subtitle_url(info, Track(lang="ru-orig", manual=True)) is None


# --- parsing and chunking ------------------------------------------------------


def test_parse_json3_real_fixture() -> None:
    segments = parse_json3(fixture_json3())
    assert segments[0] == Segment(start_ms=560, text="Всем привет.")
    assert segments[1].text.startswith("Спасибо, что пришли")
    # window-setup event (no segs) and "\n"-only events are dropped
    assert all(s.text and "\n" not in s.text for s in segments)
    assert len(segments) == 10
    assert [s.start_ms for s in segments] == sorted(s.start_ms for s in segments)


def test_parse_json3_handles_missing_fields() -> None:
    data = {"events": [{"tStartMs": 5}, {"tStartMs": 7, "segs": [{"utf8": "  "}]}, {"segs": [{"utf8": "hi"}]}]}
    assert parse_json3(data) == [Segment(start_ms=0, text="hi")]
    assert parse_json3({}) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("run install {dash} {dash} IDE", "run install -- IDE"),
        ("install {dash}{dash}ide", "install --ide"),
        ("well {dash} maybe", "well - maybe"),
        ("{dash} {dash} {dash}", "-- -"),
        ("  plain\n text  ", "plain text"),
    ],
)
def test_normalize_caption_text(raw: str, expected: str) -> None:
    assert normalize_caption_text(raw) == expected


def test_parse_json3_normalizes_dash() -> None:
    data = {"events": [{"tStartMs": 0, "segs": [{"utf8": "install"}, {"utf8": " {dash}"}, {"utf8": " {dash}"}, {"utf8": " IDE"}]}]}
    assert parse_json3(data) == [Segment(start_ms=0, text="install -- IDE")]


def test_chunk_segments() -> None:
    segments = [
        Segment(start_ms=0, text="a"),
        Segment(start_ms=9_999, text="b"),
        Segment(start_ms=10_000, text="c"),
        Segment(start_ms=35_000, text="d"),
    ]
    chunks = chunk_segments(segments, chunk_seconds=10)
    assert [(c.start_ms, c.end_ms, c.text) for c in chunks] == [
        (0, 10_000, "a b"),
        (10_000, 20_000, "c"),
        (30_000, 40_000, "d"),
    ]


def test_chunk_fixture_keeps_all_text() -> None:
    segments = parse_json3(fixture_json3())
    chunks = chunk_segments(segments, chunk_seconds=10)
    assert len(chunks) == 3
    assert " ".join(c.text for c in chunks) == " ".join(s.text for s in segments)


def test_format_ts() -> None:
    assert format_ts(0) == "00:00:00"
    assert format_ts(3_725_999) == "01:02:05"


def test_metadata_from_info() -> None:
    meta = metadata_from_info(make_info(auto={"ru-orig": [], "en": []}) | {"upload_date": "20260901"})
    assert meta.upload_date == "2026-09-01"
    assert not hasattr(meta, "automatic_captions")
    assert meta.subtitles == []


def test_is_rate_limited() -> None:
    class FakeHTTPError(Exception):
        status = 429

    class FakeDownloadError(Exception):
        def __init__(self, msg: str, exc_info: Any = None) -> None:
            super().__init__(msg)
            self.exc_info = exc_info

    assert is_rate_limited(FakeHTTPError())
    assert is_rate_limited(Exception("ERROR: Unable to download: HTTP Error 429: Too Many Requests"))
    assert is_rate_limited(FakeDownloadError("wrapped", (FakeHTTPError, FakeHTTPError(), None)))
    assert not is_rate_limited(Exception("HTTP Error 404: Not Found"))


# --- orchestration with a fake YoutubeDL ------------------------------------------


def make_info(
    video_id: str = VID,
    subs: dict[str, Any] | None = None,
    auto: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": video_id,
        "title": "Harness: новый подход",
        "channel": "Some Channel",
        "upload_date": "20260901",
        "duration": 1426,
        "language": "ru",
        "chapters": [{"title": "Intro", "start_time": 0.0, "end_time": 60.0}],
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "subtitles": subs or {},
        "automatic_captions": auto or {},
    }


class FakeYdl:
    def __init__(
        self,
        infos: dict[str, dict[str, Any] | Exception],
        subtitle_bytes: bytes = b"",
        subtitle_error: Exception | None = None,
    ) -> None:
        self.infos = infos
        self.subtitle_bytes = subtitle_bytes
        self.subtitle_error = subtitle_error
        self.opts: dict[str, Any] = {}
        self.extract_calls: list[str] = []
        self.urlopen_calls: list[str] = []

    def __call__(self, opts: dict[str, Any]) -> Self:  # acts as the factory
        self.opts = opts
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> Any:
        assert download is False
        self.extract_calls.append(url)
        info = self.infos[url.rsplit("=", 1)[-1]]
        if isinstance(info, Exception):
            raise info
        return info

    def urlopen(self, req: str) -> Any:
        self.urlopen_calls.append(req)
        if self.subtitle_error:
            raise self.subtitle_error
        return io.BytesIO(self.subtitle_bytes)


def run(tmp_path: Path, fake: FakeYdl, urls: list[str]) -> list[Any]:
    return ingest_all(
        urls,
        transcripts_dir=tmp_path / "transcripts",
        raw_dir=tmp_path / "raw",
        chunk_seconds=10,
        sleep_seconds=0,
        ydl_factory=fake,
    )


def test_ingest_auto_orig_writes_markdown(tmp_path: Path) -> None:
    auto = {
        "ru-orig": [{"ext": "json3", "url": "https://sub/ru-orig"}],
        "en": [{"ext": "json3", "url": "https://sub/en-translated"}],
    }
    fake = FakeYdl({VID: make_info(auto=auto)}, subtitle_bytes=FIXTURE.read_bytes())
    results = run(tmp_path, fake, [f"https://youtu.be/{VID}"])

    assert [(r.video_id, r.status) for r in results] == [(VID, "ok")]
    assert fake.urlopen_calls == ["https://sub/ru-orig"]  # one request, own track only
    assert fake.opts["skip_download"] is True
    assert (tmp_path / "raw" / f"{VID}.ru-orig.json3").read_bytes() == FIXTURE.read_bytes()

    md = (tmp_path / "transcripts" / f"{VID}.md").read_text(encoding="utf-8")
    front, body = md.split("---\n", 2)[1:]
    fields = {k: json.loads(v) for k, v in (line.split(": ", 1) for line in front.strip().splitlines())}
    assert fields["video_id"] == VID
    assert fields["transcript_status"] == "ok"
    assert fields["transcript_lang"] == "ru-orig"
    assert fields["transcript_manual"] is False
    assert fields["upload_date"] == "2026-09-01"
    assert fields["chunks"] == 3
    assert "automatic_captions" not in fields
    assert fields["chapters"] == [{"title": "Intro", "start": 0.0, "end": 60.0}]
    assert "## [00:00:00 - 00:00:10]" in body
    assert "Всем привет." in body


def test_ingest_sleeps_only_between_network_fetches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("video_digest.ingest.time.sleep", sleeps.append)
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "transcripts" / "ccccccccccc.md").write_text("cached")
    ids = ["aaaaaaaaaaa", "ccccccccccc", "bbbbbbbbbbb"]
    fake = FakeYdl({i: make_info(i) for i in ids})
    urls = [f"https://youtu.be/{i}" for i in ids]

    ingest_all(urls, transcripts_dir=tmp_path / "transcripts", raw_dir=tmp_path / "raw", ydl_factory=fake)
    assert len(sleeps) == 1 and 10 <= sleeps[0] <= 30  # random default, one gap between 2 fetches

    sleeps.clear()
    for i in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        (tmp_path / "transcripts" / f"{i}.md").unlink()
    ingest_all(urls, transcripts_dir=tmp_path / "transcripts", raw_dir=tmp_path / "raw", sleep_seconds=2, ydl_factory=fake)
    assert sleeps == [2]


def test_ingest_skips_cached_without_network(tmp_path: Path) -> None:
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "transcripts" / f"{VID}.md").write_text("cached")
    fake = FakeYdl({})
    results = run(tmp_path, fake, [f"https://www.youtube.com/watch?v={VID}", f"https://youtu.be/{VID}"])
    assert [(r.video_id, r.status) for r in results] == [(VID, "cached")]
    assert fake.extract_calls == [] and fake.urlopen_calls == []
    assert fake.opts == {}  # no YoutubeDL created at all


def test_ingest_no_transcript_is_recorded(tmp_path: Path) -> None:
    fake = FakeYdl({VID: make_info(auto={"en": [{"ext": "json3", "url": "translated"}]})})
    results = run(tmp_path, fake, [f"https://youtu.be/{VID}"])
    assert results[0].status == "no_transcript"
    assert fake.urlopen_calls == []
    md = (tmp_path / "transcripts" / f"{VID}.md").read_text(encoding="utf-8")
    assert 'transcript_status: "no_transcript"' in md


def test_ingest_dub_only_is_no_transcript(tmp_path: Path) -> None:
    info = make_info(auto={"ar-orig": [{"ext": "json3", "url": "dub"}], "ar": [], "en": []}) | {"language": "en-US"}
    fake = FakeYdl({VID: info})
    results = run(tmp_path, fake, [f"https://youtu.be/{VID}"])
    assert (results[0].status, results[0].detail) == ("no_transcript", "only dubbed tracks: ar-orig")
    assert fake.urlopen_calls == []
    assert "dubbed audio): ar-orig" in (tmp_path / "transcripts" / f"{VID}.md").read_text(encoding="utf-8")


def test_ingest_stops_on_rate_limit(tmp_path: Path) -> None:
    other = "zzzzzzzzzzz"
    auto = {"ru-orig": [{"ext": "json3", "url": "https://sub/ru-orig"}]}
    fake = FakeYdl(
        {VID: make_info(auto=auto), other: make_info(other, auto=auto)},
        subtitle_error=Exception("HTTP Error 429: Too Many Requests"),
    )
    with pytest.raises(RateLimitedError):
        run(tmp_path, fake, [f"https://youtu.be/{VID}", f"https://youtu.be/{other}"])
    assert len(fake.extract_calls) == 1  # second video never requested
    assert not (tmp_path / "transcripts" / f"{VID}.md").exists()


def test_ingest_error_continues(tmp_path: Path) -> None:
    other = "zzzzzzzzzzz"
    fake = FakeYdl({VID: Exception("Video unavailable"), other: make_info(other)})
    results = run(tmp_path, fake, ["https://example.com/x", f"https://youtu.be/{VID}", f"https://youtu.be/{other}"])
    assert [r.status for r in results] == ["error", "error", "no_transcript"]
    assert "Video unavailable" in results[1].detail
    assert not (tmp_path / "transcripts" / f"{VID}.md").exists()
