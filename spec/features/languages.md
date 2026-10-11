---
status: done
stage: 2
code: [src/video_digest/languages.py, src/video_digest/summarize.py, src/video_digest/config.py]
---
# Languages

## Goal
Generate and store analyses in one chosen language, independent of the video's language,
without translating technical terms.

## How it works
1. `ANALYSIS_LANGUAGE` is an ISO 639-1 code or `source`.
2. Effective language per video (`summarize.analysis_language_for`): the setting, or for
   `source` the base of the YouTube tag (`en-US` -> `en`), else of `transcript_lang`, else `en`.
3. The prompt gets the language name ("Write summary, key point texts, why, justifications,
   needs_screen_evidence and verdict_reason in <language>") and the never-translate-terms rule.
4. Script check on `summary` (`languages.check_script`): Cyrillic languages (`ru`, `uk`, `be`,
   `bg`, `sr`, `mk`, `kk`, `ky`, `tg`, `mn`) need Cyrillic letters; others need < 10% Cyrillic.
5. The effective language is part of the cache key and stored in `analyses.analysis_language`.

## Input and output
Setting -> prompt text, validation, cache key, DB column.

## CLI commands
`summarize` prints `language=<setting>` and exits 1 on an invalid `ANALYSIS_LANGUAGE`.

## Settings (.env)
`ANALYSIS_LANGUAGE` (default `en`), `DIGEST_LANGUAGE` (default `en`, see features/translation.md).

## Data
`analyses.analysis_language`; `videos.source_language`.

## Edge cases and errors
- Unknown code -> prompt says "the language with ISO 639-1 code ...".
- Latin-script languages are not distinguished from each other (lenient by design).

## Done criteria
Language tests in `tests/test_schema.py` and `tests/test_summarize.py`.

## Open questions
- Readers take the newest analysis per video regardless of language.
