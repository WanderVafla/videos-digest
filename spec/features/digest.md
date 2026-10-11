---
status: planned
stage: 5
code: []
---
# Weekly digest

## Goal
A weekly markdown file that tells the user what to watch, what to read, and what to skip.

## How it works
1. Collect the week's videos with analyses, ranking and clusters.
2. `LLM_MODEL_SMART` writes the text from stored data only (no new facts).
3. Written in `DIGEST_LANGUAGE`.

Sections: watch fully; summary is enough; themes of the week; skip.

## Input and output
Stage 2-4 data -> `digests/<week>.md`.

## CLI commands
Planned: `video-digest digest`.

## Settings (.env)
`LLM_MODEL_SMART`, `LLM_REASONING_EFFORT_SMART`, `DIGEST_LANGUAGE`.

## Data
`digests/` (exists, empty).

## Edge cases and errors
- Weeks with few videos; videos without analyses.

## Done criteria
To be defined with the stage.

## Open questions
- Week boundaries: upload date or ingest date?
