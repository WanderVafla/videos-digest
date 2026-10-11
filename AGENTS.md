# AGENTS.md

## Goal
Transcripts of neural-network videos -> LLM summaries, scores, topic clusters ->
weekly digest with a verdict per video: watch fully / summary is enough / skip.

## Structure
```
videos.txt              input: one video URL per line, # = comment
data/raw/               raw subtitle files, json3 (git-ignored)
data/transcripts/       <video_id>.md: frontmatter + time chunks (git-ignored)
data/db.sqlite          videos + analyses, the LLM cache (git-ignored)
data/review_blind.md    blind human ratings (tracked); review_model.md ignored
topics.txt              topic taxonomy `slug: description` (tracked, grows)
digests/                generated weekly digests
src/video_digest/
  config.py             settings from .env (paths, Qdrant URL, LLM config)
  cli.py                typer app, `uv run video-digest <command>`
  ingest.py             stage 1: videos.txt -> transcripts (yt-dlp)
  llm.py                llm(): the only way to call an LLM
  summarize.py          stage 2 (+ transcripts, schema, features, topics, db)
  review.py, translate.py  export-review/calibrate; display translation (cached)
  prompts/              summarize_v2.md (PROMPT_VERSION), translate_v1.md, glossary.txt
tests/                  pytest; fixtures/fakes only, never real network
```

## Rules
- Python 3.12, managed with uv (`uv add`, `uv run`). No pip.
- Type hints everywhere; pydantic for schemas; settings only via `config.py`.
- Every LLM call goes through `llm()` (OpenAI-compatible client); base URL, key
  and models (FAST: summaries, SMART: clusters/digest) only from `config.py`.
- Cache by sha256(transcript + PROMPT_VERSION + model + analysis language) in
  db.sqlite; never re-call for a cached key. A prompt change = new file + version.
- ANALYSIS_LANGUAGE (code or "source") = language of stored analyses;
  DIGEST_LANGUAGE = what the user reads, via translate() (not in the cache key).
  Never translate terms (names, commands, acronyms); topic slugs are English.
- topics.txt is mutable state, NOT in the cache key: results depend on processing
  order, and --force re-runs use the grown list. Code, not the model, decides
  what is new (exact/normalized/near-duplicate match).
- demo_value uses text hints only and is flagged by demo_confidence.
- Tests never call real APIs: fake clients only.
- Every feature (command, option, setting, file) is documented in README.md.
- YouTube: never request translated auto-captions (HTTP 429); one own-language
  track per video. All yt-dlp calls go through `make_ydl()` in ingest.py.
- Never commit `.env`, transcripts, the database or Qdrant data (data/qdrant/).

## Stages
Work strictly one stage at a time. Never build ahead of the current stage:
no scripts, modules or dependencies for future stages until asked.
Current stage: 2 (summaries, scores, languages, topics).
