---
status: done
stage: 2
code: [src/video_digest/review.py, src/video_digest/cli.py]
---
# Calibration

## Goal
Measure how far the model's scores and verdicts are from the user's blind ratings.

## How it works
1. Parse `data/review_blind.md`; keep entries with a valid `my_verdict`; `?` scores skipped.
2. Keep ratings of the current `PROMPT_VERSION`; match by video id with the newest analysis of
   that prompt version and model.
3. Report: mean absolute difference per score (`value` = practical_value, `density` =
   information_density, `demo` = demo_value), verdict agreement in %, confusion table
   (rows: user, columns: model), 5 largest disagreements
   (distance = 5 x verdict-rank gap + sum of score differences).

## Input and output
`data/review_blind.md` + `analyses` -> text report on stdout.

## CLI commands
`video-digest calibrate [--model MODEL]` (default `LLM_MODEL_FAST`; empty = any model). The
first line names the compared model and prompt version. Exit 1 if the blind file is missing.

## Settings (.env)
`LLM_MODEL_FAST` (default model).

## Data
Reads only; no LLM calls.

## Edge cases and errors
- No filled entries -> "Nothing to compare yet".
- Ratings for another prompt version are ignored; ratings without a matching analysis count as
  rated but not matched.

## Done criteria
Calibration tests in `uv run pytest tests/test_review.py`.

## Working on this code
- Read-only and no LLM calls; keep it that way.
- Score names in the blind file (`value`, `density`, `demo`) map to schema fields in
  `review.SCORE_FIELDS`; change both sides together.
- Run: `uv run pytest tests/test_review.py`.

## Open questions
- What agreement / MAE is good enough (status.md).
