# video-digest

Turns YouTube videos about neural networks and AI tools (any language) into a weekly reading
list: each video is transcribed, summarized and scored by an LLM, tagged with topics, and gets
a verdict: **watch_full**, **summary_enough** or **skip**.

Current stage: 2 of 5 (ingest + per-video analysis). Clustering and the weekly digest come later.

## Setup

```bash
uv sync                        # create .venv and install dependencies
cp .env.example .env           # then fill in the settings below
docker compose up -d qdrant    # vector DB, only needed from later stages
```

| Setting | Default | Meaning |
|---|---|---|
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible API, e.g. `https://openrouter.ai/api/v1` |
| `LLM_API_KEY` | – | API key (never logged) |
| `LLM_MODEL_FAST` | – | Model for summaries and translations, e.g. `anthropic/claude-haiku-5.5` |
| `LLM_MODEL_SMART` | – | Model for later stages (cluster names, digest) |
| `LLM_REASONING_EFFORT_FAST` / `_SMART` | `low` / `medium` | `reasoning.effort` per model; empty = not sent |
| `LLM_MAX_OUTPUT_TOKENS` | `14000` | `max_tokens` per call, reasoning included; `0` = not sent (OpenRouter then may answer 402 on a low balance) |
| `ANALYSIS_LANGUAGE` | `en` | Language of stored analyses: ISO 639-1 code or `source` (the video's own language) |
| `DIGEST_LANGUAGE` | `en` | Language you read in (review files now, the digest later) |
| `QDRANT_URL` | `http://localhost:6333` | Vector DB, later stages |
| `YTDLP_JS_RUNTIME` | `bun` | JS runtime yt-dlp uses for YouTube: `deno`, `node`, `bun`, `quickjs` |

## Workflow

```bash
uv run video-digest ingest          # 1. fetch transcripts
uv run video-digest summarize       # 2. analyze them with the LLM
uv run video-digest export-review   # 3. write review files
#                                     4. fill in data/review_blind.md
uv run video-digest calibrate       # 5. compare your ratings with the model
```

### `ingest`: transcripts

Reads `videos.txt` (one URL per line; empty lines and `#` comments ignored; `watch?v=`,
`youtu.be/` and `/shorts/` links) and writes `data/transcripts/<video_id>.md`: frontmatter
(title, channel, date, duration, language, chapters, which caption track was used) plus the
text in time chunks with headings like `## [00:02:00 - 00:04:00]`.

- Uses only the video's own captions: manual subtitles in the video's language, otherwise the
  original-language auto track. Auto-dubbed tracks and translated captions are never used
  (YouTube rate-limits translations with HTTP 429). One caption request per video.
- Videos that already have a `.md` file are skipped without any network call. Delete the file to re-fetch.
- A video without usable captions gets a file with `transcript_status: "no_transcript"`.
- Pauses between videos are random 10-30 s. On HTTP 429 the run stops (exit code 2).
- Raw caption files are kept in `data/raw/` (json3).

| Option | Default | |
|---|---|---|
| `--videos PATH` | `videos.txt` | URL list |
| `--chunk-seconds N` | `120` | Chunk length |
| `--sleep S` | random 10-30 | Fixed pause between fetched videos (`0` = none) |

### `summarize`: analysis per video

Sends each transcript (status `ok`) to `LLM_MODEL_FAST` in one request and stores the result in
`data/db.sqlite`. Each analysis has: summary (5-7 sentences), 4-8 key points with timestamps,
3-7 topics, entities, three scores 0-10 with one-sentence justifications (`practical_value`,
`information_density`, `demo_value`), `demo_confidence`, `needs_screen`, up to 3 best moments,
and the verdict with a reason.

- **Cache:** results are keyed by sha256(transcript + prompt version + model + analysis
  language). A cached video costs nothing; changing any of these four triggers a new call.
- **Languages:** prose is written in `ANALYSIS_LANGUAGE`. Names of tools, models and companies,
  commands, flags, file names, acronyms and established English terms are never translated.
- **Timestamps** must be chunk-heading starts, so they cannot be invented.
- **Demo hints:** the model only sees text, so code counts weak signals and passes them in:
  screen-pointing phrases ("as you can see", "смотрите"), shell commands and flags, long
  silences in the captions, and tutorial words in the title. `demo_confidence` says how
  much to trust `demo_value`.
- **Speech-recognition errors** in names are corrected using `src/video_digest/prompts/glossary.txt`.
- **Robust API calls:** structured output with fallbacks (json_schema -> json_object -> plain
  text), unsupported parameters are dropped, invalid answers are sent back for correction (up to
  2 times), 429/5xx are retried with backoff. Transcripts above ~100k tokens are summarized in
  parts and merged.
- **Output limit:** every call sends `max_tokens` = `LLM_MAX_OUTPUT_TOKENS` (reasoning tokens
  count against it). A cut-off answer is retried once with double the limit, then the video fails.
- One progress line per video (verdict, tokens, cost when the provider reports it, topic
  changes); totals at the end. A failed video is logged and the run continues.
- HTTP 401/402/403 (bad key, no balance, no model access) stop the run at once with a hint and
  exit code 2; analyses finished before that are kept.

| Option | |
|---|---|
| `--limit N` | Analyze at most N videos that need an API call |
| `--force` | Re-run cached videos and overwrite their analyses |
| `--id VIDEO_ID` | Only this video |

Old prompt versions stay in the DB until you remove them:

```bash
uv run video-digest prune --prompt-version summarize_v1        # dry run: count, models, cost
uv run video-digest prune --prompt-version summarize_v1 --yes  # delete them (never the current version)
```

### Topics: `topics.txt`

The topic list lives in `topics.txt` (tracked by git), one `slug: description` per line, slugs in
lowercase English kebab-case. It starts empty and grows while `summarize` runs:

- The prompt lists existing topics, most used first (up to 150). The model should reuse them
  and add at most 2 new ones per video; the first video creates the initial list.
- Code, not the model, decides what is new: exact match, then normalized match
  (case, separators, trailing plural `s`), then near-duplicates (similarity >= 0.85) are mapped
  to the existing slug; only the rest is appended.
- The list is not part of the cache key, so results depend on processing order.

```bash
uv run video-digest topics                    # slugs with usage counts
uv run video-digest topics merge OLD NEW      # rewrite OLD -> NEW in stored analyses and topics.txt
```

Both use only analyses of the current prompt version.

### `export-review` and `calibrate`: check the model against your judgment

`export-review` writes two files for the current prompt version and one model (`--model M`,
default `LLM_MODEL_FAST`, the same default as `calibrate`); the model and prompt version are
printed and recorded in the first line of `data/review_blind.md`:

- `data/review_blind.md`: per video only title, link, summary and key points (translated to
  `DIGEST_LANGUAGE` if it differs from the analysis language; translations are cached), plus
  lines to fill in: `my_verdict: ?` and optional `my_scores: value=? density=? demo=?`.
  Re-exporting keeps what you already filled in.
- `data/review_model.md` (git-ignored): the model's scores, justifications, `demo_confidence`
  and verdict, in the analysis language. Read it after rating.

`calibrate` reads your filled entries (those still at `?` are skipped) and prints the mean
absolute score difference, verdict agreement, a confusion table and the 5 biggest
disagreements. `--model M` picks the model to compare (default: `LLM_MODEL_FAST`).

## Data and files

| Path | Content | In git |
|---|---|---|
| `videos.txt` | Video URLs | no |
| `data/transcripts/`, `data/raw/` | Transcripts, raw captions | no |
| `data/db.sqlite` | Videos, analyses (the LLM cache), translations | no |
| `data/review_blind.md` | Your blind ratings | yes |
| `data/review_model.md` | Model scores for review | no |
| `topics.txt` | Topic taxonomy | yes |
| `src/video_digest/prompts/` | Prompts (`summarize_v2.md`, `translate_v1.md`) and glossary | yes |

## Development

```bash
uv run pytest                                          # offline tests (fakes, no network)
uvx pyright --pythonpath .venv/bin/python src tests    # type check
```

Project rules for coding agents: `AGENTS.md`.
