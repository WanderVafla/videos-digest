---
status: done
stage: 2
code: [src/video_digest/review.py, src/video_digest/cli.py]
---
# Review export

## Goal
Let the user rate videos blind, then compare with the model.

## How it works
1. Take the newest analysis per video for the current `PROMPT_VERSION` (any model).
2. Blind file: title, video_id, prompt_version, url, summary and key points; translated to
   `DIGEST_LANGUAGE` when it differs from the row's `analysis_language` (two calls per video,
   cached); then `my_verdict: ?` and `my_scores: value=? density=? demo=?`.
3. Keep already filled `my_verdict`/`my_scores` lines (matched by video_id + prompt_version).
4. Model file: scores, justifications, demo_confidence, needs_screen, verdict, topics, model and
   analysis language; untranslated.

## Input and output
`analyses` -> `data/review_blind.md`, `data/review_model.md` (formats: data-contracts.md).

## CLI commands
`video-digest export-review`: exits 1 on an invalid `DIGEST_LANGUAGE` or any error (e.g. a
failed translation).

## Settings (.env)
`DIGEST_LANGUAGE`; `LLM_MODEL_FAST` when translating.

## Data
Reads `videos`, `analyses`; writes `translations` when translating.

## Edge cases and errors
- No analyses -> both files with headers only, count 0.
- review_blind.md is tracked in git; review_model.md is git-ignored.

## Done criteria
Export tests in `uv run pytest tests/test_review.py`.

## Open questions
- Not filtered by model, unlike `calibrate --model` (status.md).
