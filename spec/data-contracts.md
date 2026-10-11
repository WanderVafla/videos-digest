# Data contracts

Formats the stages depend on. Change this file first, then the code.

## videos.txt (input, not in git)

One URL per line; empty lines and lines starting with `#` are ignored. Recognized:
`youtube.com/watch?v=<id>`, `youtu.be/<id>`, `youtube.com/shorts/<id>`; id = 11 chars `[A-Za-z0-9_-]`.
Code: `ingest.read_video_urls`, `ingest.video_id_from_url`.

## Transcript: data/transcripts/<video_id>.md

Written by `ingest.render_markdown`, read by `transcripts.load_transcript`.

Frontmatter between `---` lines; one `key: value` per line; every value is JSON (valid YAML).

| Key | Type | Meaning |
|---|---|---|
| `video_id` | str | YouTube id |
| `title` | str | |
| `channel` | str \| null | channel, or uploader |
| `url` | str | `webpage_url` |
| `upload_date` | str \| null | `YYYY-MM-DD` |
| `duration` | int \| null | seconds |
| `language` | str \| null | YouTube language tag, e.g. `en-US`, `ru` |
| `transcript_status` | str | `ok` or `no_transcript` |
| `transcript_lang` | str \| null | chosen caption track key, e.g. `ru-orig`, `en` |
| `transcript_manual` | bool \| null | true = manual subtitles, false = auto captions |
| `chunk_seconds` | int | chunk length (default 120) |
| `chunks` | int | number of chunks |
| `chapters` | list of `{title, start, end}` | seconds (float) |
| `subtitles` | list[str] | keys of manual subtitle tracks |
| `ingested_at` | str | ISO 8601 UTC |

Body: `# <title>`, then per chunk a heading and its text:

```
## [00:02:00 - 00:04:00]

text of all caption events that start in this window
```

- Heading regex (reader): `^## \[(\d{2}):(\d{2}):(\d{2}) - \d{2}:\d{2}:\d{2}\]$`.
- Windows are `[k*size, (k+1)*size)`; empty windows are skipped.
- Caption text: whitespace collapsed; `{dash} {dash}` -> `--`, `{dash}` -> `-`.
- `no_transcript` files have no chunks, only a note (and skipped dubbed tracks, if any).
- `transcripts.load_transcripts` returns only files with status `ok` and at least one chunk.

## Raw captions: data/raw/<video_id>.<transcript_lang>.json3 (not in git)

The YouTube json3 response, saved unchanged: `{"events": [...]}`; an event has `tStartMs`,
`dDurationMs`, optional `segs: [{utf8, tOffsetMs?}]`. Events without `segs` or with only
whitespace are not speech. Used by stage 1 (parsing) and `features.long_pauses`; optional.

## data/ingest_failed.txt

Does not exist. Failed videos are only printed by `ingest`; nothing records them on disk.

## Analysis JSON (`VideoAnalysis`, src/video_digest/schema.py)

Stored in `analyses.analysis_json`; also the JSON Schema for structured output. All fields are
required; no extra fields (`additionalProperties: false`).

| Field | Type | Meaning |
|---|---|---|
| `source_language` | str `^[a-z]{2}$` | ISO 639-1 language of the transcript |
| `summary` | str | 5-7 sentences in the analysis language (validated loosely: 3-10 sentence ends) |
| `key_points` | list of `{text, timestamp}`, 4-8 | in transcript order |
| `topics` | list of `{slug, description}`, 3-7, unique slugs | stored after mapping to `topics.txt`; description from the file |
| `entities` | list[str] | tools, models, companies, libraries; canonical names |
| `scores.practical_value` | `{score: int 0-10, justification: str}` | |
| `scores.information_density` | same | |
| `scores.demo_value` | same | estimated from text and demo hints |
| `demo_confidence` | `low` \| `high` | trust in `demo_value` |
| `needs_screen` | bool | main value is on screen |
| `needs_screen_evidence` | str | short paraphrase, no quotes |
| `best_moments` | list of `{timestamp, why}`, max 3 | |
| `verdict` | `watch_full` \| `summary_enough` \| `skip` | |
| `verdict_reason` | str | one sentence |

- `timestamp`: `^(?:\d{1,2}:[0-5]\d:[0-5]\d|[0-5]?\d:[0-5]\d)$` ("m:ss", "mm:ss", "h:mm:ss").
- Checks with validation context (in `summarize`): every timestamp equals a chunk-heading
  start; summary script fits the analysis language (`languages.check_script`); new-topic rules
  (`topics.check_new_topics`).
- `analysis_language` is not in the JSON; it is the `analyses.analysis_language` column.
- Old `summarize_v1` rows (fields `summary_ru`, `text_ru`, `why_ru`, `verdict_reason_ru`, topics
  as plain strings) may exist in a local DB. Every reader of `analyses` filters by prompt version
  (`export-review`, `calibrate`, topic usage, `topics`, `topics merge`: the current one). Stored
  topics are read via `topics.stored_topic`, which accepts both shapes. `video-digest prune
  --prompt-version summarize_v1 --yes` deletes such rows.

## SQLite: data/db.sqlite (src/video_digest/db.py, not in git)

Tables are created on connect; missing columns are added by `db.MIGRATIONS`.

**videos**: one row per analyzed video.

| Column | Meaning |
|---|---|
| `id` TEXT PK | video id |
| `title`, `channel`, `url`, `upload_date` | from the transcript |
| `duration_seconds` INTEGER | frontmatter duration, or last chunk start + chunk length |
| `source_language` TEXT | from the analysis, before that from the YouTube tag |
| `transcript_path` TEXT | path of the `.md` file |

**analyses**: one row per (video, prompt version, model, analysis language).

| Column | Meaning |
|---|---|
| `id` INTEGER PK | autoincrement |
| `video_id` | -> videos.id |
| `prompt_version` | e.g. `summarize_v2` |
| `model` | model id as sent to the API |
| `cache_key` TEXT UNIQUE | see below |
| `analysis_json` | `VideoAnalysis` JSON |
| `hints_json` | `DemoHints` JSON (see features/demo-hints.md) |
| `created_at` | ISO 8601 UTC |
| `tokens_in`, `tokens_out` | summed over all calls for this video (retries, parts) |
| `cost_usd` REAL \| NULL | from `usage.cost` (OpenRouter credits = USD), NULL if not reported |
| `analysis_language` | effective ISO 639-1 code |

Saving deletes rows with the same (video, prompt version, model, analysis language) or the same
cache key. Readers take the newest row per video (`created_at`, then `id`) within one prompt
version. `prune --prompt-version V --yes` deletes all rows of V (never the current version).

**translations**: display-translation cache.

| Column | Meaning |
|---|---|
| `cache_key` TEXT PK | see below |
| `target_language`, `model`, `text`, `created_at` | translated text and how it was made |

## Cache keys

- Analysis: `sha256(transcript_body + "\0" + PROMPT_VERSION + "\0" + model + "\0" + analysis_language)`,
  transcript_body = everything after the frontmatter. `summarize.cache_key`.
- Translation: `sha256(text + "\0" + target_language + "\0" + model + "\0" + TRANSLATION_PROMPT_VERSION)`.
  `translate.translation_cache_key`.
- Not in any key: `topics.txt`, `DIGEST_LANGUAGE`, demo hints, glossary.

## topics.txt (tracked)

One `slug: description` per line, description in English. Slug: `^[a-z0-9]+(-[a-z0-9]+)*$`, 1-3
words (enforced for new slugs). Lines that do not match are skipped; on duplicate slugs the first
wins. Missing or empty file = empty taxonomy. Rewritten atomically (`topics.txt.tmp` + rename).

## data/review_blind.md (tracked once created)

```
<!-- exported: model=<model or "any"> prompt_version=<prompt_version> -->
# Blind review (<prompt_version>, shown in <digest_language>)
...instructions...

## <title>

video_id: <id>
prompt_version: <prompt_version>
url: <url>

<summary in DIGEST_LANGUAGE>

- <timestamp> <key point>          (one line per key point)

my_verdict: ?                       (watch_full | summary_enough | skip)
my_scores: value=? density=? demo=? (optional, 0-10 each)
```

The first line records what was exported (`export-review --model`); it is not parsed.
Entries start at `## `. Parsed keys (line start only): `video_id`, `prompt_version`, `my_verdict`,
`my_scores`. Entries with an invalid `my_verdict` (e.g. `?`) are ignored; `?` scores are skipped.
Re-export keeps filled `my_verdict`/`my_scores` by (video_id, prompt_version).

## data/review_model.md (git-ignored)

Per video: `## <title>`, then lines `video_id`, `url`, `model: <m>; analysis_language: <l>`,
`verdict: <v> (<reason>)`, `scores: value=N density=N demo=N`,
`demo_confidence: <c>; needs_screen: <bool> (<evidence>)`, one justification line per score,
`topics: <slugs>`. Written in the analysis language. Not parsed by code.

## Settings (.env)

Read only in `src/video_digest/config.py`; missing keys never raise. Defaults: `.env.example`.

| Key | Used by |
|---|---|
| `LLM_BASE_URL`, `LLM_API_KEY` | `llm.get_client` |
| `LLM_MODEL_FAST` | `summarize`, translation, `calibrate` and `export-review` default model |
| `LLM_MODEL_SMART` | only to choose `LLM_REASONING_EFFORT_SMART`; stages 4-5 later |
| `LLM_REASONING_EFFORT_FAST`, `LLM_REASONING_EFFORT_SMART` | `llm.default_reasoning_effort` |
| `LLM_MAX_OUTPUT_TOKENS` | int, default 14000; `max_tokens` of every `llm()` call (reasoning included); `0` = not sent |
| `ANALYSIS_LANGUAGE` | ISO 639-1 or `source`; `summarize` |
| `DIGEST_LANGUAGE` | ISO 639-1; `export-review` |
| `QDRANT_URL` | not used yet (stage 3) |
| `YTDLP_JS_RUNTIME` | `ingest.ydl_opts` (`js_runtimes`) |

Paths are fixed in config.py: `data/`, `data/transcripts/`, `data/raw/`, `data/db.sqlite`,
`digests/`, `videos.txt`, `topics.txt`.
