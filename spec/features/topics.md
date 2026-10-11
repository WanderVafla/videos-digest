---
status: done
stage: 2
code: [src/video_digest/topics.py, src/video_digest/summarize.py, src/video_digest/cli.py]
---
# Topics

## Goal
A shared, growing topic taxonomy so Russian and English videos get the same English tags.

## How it works
1. Before each video: load `topics.txt` and usage counts (newest analysis per video of the
   current `PROMPT_VERSION`; other versions are ignored).
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
- `video-digest topics`: slugs with usage counts (current prompt version); marks slugs missing
  from topics.txt.
- `video-digest topics merge OLD NEW`: rewrites OLD -> NEW in the stored analyses of the current
  prompt version and in topics.txt (NEW keeps its description, or takes OLD's), removes OLD.
  Rows of other prompt versions are left unchanged (delete them with `prune`).

## Settings (.env)
None. Path fixed: `topics.txt` in the project root.

## Data
`topics.txt` (local, git-ignored; a fresh checkout starts empty), `analyses.analysis_json.topics`.

## Edge cases and errors
- Missing/empty file = empty taxonomy; the first video seeds it without the 2-new limit.
- Invalid lines in topics.txt are skipped.
- Not in the cache key: results depend on processing order.
- Stored topics are read only through `topics.stored_topic` / `stored_topics`: a `{slug,
  description}` object, or a plain string (summarize_v1); anything else, or invalid JSON, is
  skipped, so legacy rows never crash a reader.

## Done criteria
`uv run pytest tests/test_topics.py` and the topic tests in `tests/test_summarize.py`.

## Working on this code
- Code decides what is new; keep `resolve` and `check_new_topics` pure.
- Write `topics.txt` only through `save_topics`/`append_topics` (atomic temp file + rename).
- Tests must pass `topics_path` / a temp file; never write the real `topics.txt` in tests.
- `stored_usage` and `merge` take a prompt version; callers pass `summarize.PROMPT_VERSION`
  (decision D17). Never read `analysis_json["topics"]` directly.
- Run: `uv run pytest tests/test_topics.py tests/test_summarize.py`.

## Open questions
- None.
