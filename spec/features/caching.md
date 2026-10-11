---
status: done
stage: 2
code: [src/video_digest/summarize.py, src/video_digest/translate.py, src/video_digest/db.py]
---
# Caching

## Goal
Never pay twice for the same LLM input.

## How it works
1. Analyses: before any call, `summarize` computes `cache_key(transcript body, PROMPT_VERSION,
   model, analysis language)` and skips the video if `analyses.cache_key` exists.
2. `--force` ignores the cache; saving replaces the row for the same video, prompt version,
   model and analysis language.
3. Translations: `translate()` looks up `translations.cache_key` before calling the LLM.
4. Ingest has its own file cache: an existing `data/transcripts/<id>.md` is never re-fetched.

## Input and output
Keys and tables: data-contracts.md ("Cache keys", "SQLite").

## CLI commands
`summarize --force` bypasses the analysis cache. Delete a transcript file to re-ingest.

## Settings (.env)
`LLM_MODEL_FAST`, `ANALYSIS_LANGUAGE` change analysis keys; `DIGEST_LANGUAGE` does not.

## Data
`analyses.cache_key` (UNIQUE), `translations.cache_key` (PK).

## Edge cases and errors
- Topic list, demo hints and glossary are not in the key (decisions D5, D9).
- A failed video stores nothing, so it is retried on the next run.

## Done criteria
`tests/test_summarize.py` (cached run makes no call; new language/model/transcript makes one),
`tests/test_translate.py` (cache hit).

## Working on this code
- Anything that changes analysis output must be in `cache_key` or come with a new
  `PROMPT_VERSION`; otherwise stale results are served.
- Do not add the topic list or `DIGEST_LANGUAGE` to the key (D6, D9).
- A key change invalidates every stored analysis: say so in the PR and in status.md.
- Run: `uv run pytest tests/test_summarize.py tests/test_translate.py`.

## Open questions
- None.
