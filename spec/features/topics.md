---
status: done
stage: 2
code: [src/video_digest/topics.py, src/video_digest/summarize.py, src/video_digest/cli.py]
---
# Topics

## Goal
A shared, growing topic taxonomy so Russian and English videos get the same English tags.

## How it works
1. Before each video: load `topics.txt` and usage counts (newest analysis per video).
2. The prompt lists up to 150 topics, most used first; the model returns 3-7 `{slug, description}`.
3. Validation (`check_new_topics`), feedback to the model on failure:
   at most 2 new topics (only once the list is non-empty), new slugs 1-3 words with a
   description, at least 3 distinct topics after mapping.
4. Resolution (`resolve`): exact slug -> normalized (lowercase, `-` separators, trailing plural
   `s` stripped) -> near-duplicate (difflib ratio >= 0.85) -> new.
5. Mapped slugs are replaced by the existing ones; new topics are appended atomically.

## Input and output
`topics.txt` (data-contracts.md); topics inside `analysis_json`.

## CLI commands
- `video-digest topics`: slugs with usage counts; marks slugs missing from topics.txt.
- `video-digest topics merge OLD NEW`: rewrites OLD -> NEW in all stored analyses and in
  topics.txt (NEW keeps its description, or takes OLD's), removes OLD.

## Settings (.env)
None. Path fixed: `topics.txt` in the project root.

## Data
`topics.txt` (tracked in git), `analyses.analysis_json.topics`.

## Edge cases and errors
- Missing/empty file = empty taxonomy; the first video seeds it without the 2-new limit.
- Invalid lines in topics.txt are skipped.
- Not in the cache key: results depend on processing order.

## Done criteria
`uv run pytest tests/test_topics.py` and the topic tests in `tests/test_summarize.py`.

## Working on this code
- Code decides what is new; keep `resolve` and `check_new_topics` pure.
- Write `topics.txt` only through `save_topics`/`append_topics` (atomic temp file + rename).
- Tests must pass `topics_path` / a temp file; never write the real `topics.txt` in tests.
- `merge` rewrites every analysis row, including old prompt versions.
- Run: `uv run pytest tests/test_topics.py tests/test_summarize.py`.

## Open questions
- `topics merge` likely fails on old `summarize_v1` rows (topics as strings).
