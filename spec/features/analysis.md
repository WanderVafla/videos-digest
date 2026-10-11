---
status: done
stage: 2
code: [src/video_digest/summarize.py, src/video_digest/schema.py, src/video_digest/transcripts.py, src/video_digest/prompts/summarize_v2.md]
---
# Analysis (schema, scoring, verdict)

## Goal
Turn each transcript into a structured `VideoAnalysis`: summary, key points, topics, entities,
three scores, demo confidence, best moments and a verdict.

## How it works
1. Load transcripts with status `ok` (`transcripts.load_transcripts`).
2. Skip videos whose cache key exists (features/caching.md) unless `--force`.
3. Compute demo hints (features/demo-hints.md) and the topic list block (features/topics.md).
4. Build messages from `prompts/summarize_v2.md` (SYSTEM + USER sections): glossary, target
   language, topics, metadata, hints, the whole transcript with chunk headings.
5. Above ~100k estimated tokens (len/3.5): split into parts of whole chunks, analyze each, then
   one MERGE call.
6. Call `llm()` with `VideoAnalysis` and a validation context: allowed timestamps (chunk starts),
   analysis language, new-topic rules. Invalid answers go back to the model (2 retries).
7. Map topics onto `topics.txt`, append new ones, save `videos` and `analyses` rows.

Scoring rubric, verdict rules and wording: `src/video_digest/prompts/summarize_v2.md`.
Field list: data-contracts.md.

## CLI commands
`video-digest summarize [--limit N] [--force] [--id VIDEO_ID]`
- Needs `LLM_API_KEY` and `LLM_MODEL_FAST`; checks `ANALYSIS_LANGUAGE`.
- Progress per video: verdict, tokens, cost, topic notes; totals at the end.
- `--limit` counts only videos that need a call. Exit 1 if any video failed.

## Settings (.env)
`LLM_MODEL_FAST`, `LLM_REASONING_EFFORT_FAST`, `ANALYSIS_LANGUAGE`, plus llm-client settings.

## Data
`videos`, `analyses` (data-contracts.md); `topics.txt`.

## Edge cases and errors
- Any exception for one video (API, validation) -> `error` line, run continues.
- Timestamps not equal to a chunk start, wrong script, too many new topics -> validation error
  fed back to the model.
- `PROMPT_VERSION = "summarize_v2"`; a prompt change needs a new file and version.

## Done criteria
`uv run pytest tests/test_summarize.py tests/test_schema.py` passes; `summarize --limit 1` on a
real key writes one `analyses` row (paid, user only).

## Open questions
- Summary length is validated as 3-10 sentences, the prompt asks for 5-7.
- No `max_tokens` (status.md).
