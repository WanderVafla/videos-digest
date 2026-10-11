---
status: done
stage: 1
code: [src/video_digest/ingest.py, src/video_digest/cli.py]
---
# Ingest

## Goal
Fetch the caption text of every video in `videos.txt` and save it as a chunked transcript.

## How it works
1. Read `videos.txt`, extract video ids, drop duplicates.
2. Skip ids whose `data/transcripts/<id>.md` exists (no network call; no YoutubeDL is created
   if nothing is pending).
3. Per remaining video: `extract_info(download=False)` through `make_ydl()`.
4. `pick_track`: manual subtitles in the video's language -> its `-orig` auto track -> any manual
   subtitles -> none. Language unknown: first manual, else first `-orig`. See decisions D1.
5. Fetch the json3 URL of that track with one `urlopen`; save it to `data/raw/`.
6. Parse events, normalize text (`{dash}`), group into fixed windows, write the `.md`.
7. Sleep a random 10-30 s before the next fetched video.

## Input and output
`videos.txt` -> `data/transcripts/<id>.md`, `data/raw/<id>.<lang>.json3` (data-contracts.md).

## CLI commands
`video-digest ingest [--videos PATH] [--chunk-seconds N (>=10, 120)] [--sleep S]`.
One line per video: `ok`, `no_transcript`, `cached` or `error` plus detail.
Exit codes: 0 ok; 1 any error; 2 rate-limited (HTTP 429). No URLs: message, exit 0.

## Settings (.env)
`YTDLP_JS_RUNTIME` (default `bun`) -> yt-dlp `js_runtimes`.

## Data
Transcript and raw json3 files; nothing in SQLite.

## Edge cases and errors
- Not a YouTube URL -> `error`, run continues.
- Only dubbed `-orig` tracks -> `no_transcript` file listing the skipped tracks.
- No json3 format for the chosen track -> `error`, no file.
- HTTP 429 anywhere (also wrapped in `DownloadError`) -> stop the whole run, no file for that video.
- Any other error -> `error`, no file, next video. Errors are not written to disk.

## Done criteria
`uv run pytest tests/test_ingest.py` passes (fixtures and a fake YoutubeDL; no network).

## Working on this code
- Never run `ingest` or call yt-dlp yourself; tests use `tests/fixtures/*.json3` and the
  `FakeYdl` in `tests/test_ingest.py`.
- Build every `YoutubeDL` through `make_ydl()`, options through `ydl_opts()` (D3).
- `pick_track` must only return the video's own-language track; one `urlopen` per video (D1).
- The `.md` format is a contract: update data-contracts.md first, then `render_markdown` and
  `transcripts.py` together. Existing files are not rewritten (they are the cache).
- Run: `uv run pytest tests/test_ingest.py`.

## Open questions
- The video description is not saved (title keywords use the title only).
