---
status: done
stage: 2
code: [src/video_digest/features.py]
---
# Demo hints

## Goal
Give the model weak, text-only evidence about how much of the value is on screen.

## How it works
`compute_hints(text, title, duration_seconds, raw_path) -> DemoHints`, pure functions:
- `pointer_phrases`, `pointer_phrases_per_min`: regex lists for English and Russian
  ("as you can see", "click", "смотрите", "нажимаем", ...).
- `command_mentions`: npx, npm, pip, uv, git, docker, cd, "dash dash", `--flags`, file names.
- `long_pauses`: gaps > 8 s between speech events in the raw json3 (count, total seconds, share
  of duration); `null` if the raw file is missing.
- `title_keywords`: tutorial/demo/setup words (en, ru) found in the title.
The prompt treats them as weak evidence (rules in `prompts/summarize_v2.md`).

## Input and output
Transcript body, title, duration, `data/raw/<id>.<transcript_lang>.json3` -> `DemoHints`.

## CLI commands
None; computed inside `summarize`.

## Settings (.env)
None.

## Data
`analyses.hints_json`.

## Edge cases and errors
- Unknown duration -> per-minute rate 0, pause share `null`.
- Auto-caption events overlap, so long pauses are rare (6 of 41 current videos have any).

## Done criteria
`uv run pytest tests/test_features.py` (both languages, trimmed json3 fixture, missing file).

## Working on this code
- Pure functions, no LLM; the only I/O is reading the raw json3 file.
- Hints are prompt input but not in the cache key: if old analyses should change, bump
  `PROMPT_VERSION`.
- Add regex patterns for both English and Russian, as whole words, with a test each.
- Run: `uv run pytest tests/test_features.py`.

## Open questions
- Title only; the description is not ingested.
