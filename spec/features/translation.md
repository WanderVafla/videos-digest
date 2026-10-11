---
status: done
stage: 2
code: [src/video_digest/translate.py, src/video_digest/prompts/translate_v1.md, src/video_digest/db.py]
---
# Display translation

## Goal
Show stored text in `DIGEST_LANGUAGE` without re-running the analysis.

## How it works
`translate(text, target_language, source_language=ANALYSIS_LANGUAGE, *, conn, model, llm_fn)`
1. Same language or blank text -> return unchanged, no call.
2. Look up the `translations` cache.
3. Otherwise call `llm()` with `LLM_MODEL_FAST` and `prompts/translate_v1.md` (never-translate
   rule, keep numbers, timestamps and formatting, return only the text, glossary).
4. Store and return the stripped result.

## Input and output
Text in the analysis language -> text in the target language.

## CLI commands
Used by `export-review` (summary and key points of the blind file).

## Settings (.env)
`DIGEST_LANGUAGE`, `LLM_MODEL_FAST`, llm-client settings.

## Data
`translations` table; key with `TRANSLATION_PROMPT_VERSION = "translate_v1"` (data-contracts.md).

## Edge cases and errors
- Empty answer -> `ValueError`, nothing cached. `export-review` then stops with an error;
  finished translations stay cached.

## Done criteria
`uv run pytest tests/test_translate.py`.

## Open questions
- None.
