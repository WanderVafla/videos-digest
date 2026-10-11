# AGENTS.md

## Goal
video-digest: a Python system that takes transcripts of videos about neural
networks (Russian and English), produces LLM summaries and scores, clusters
topics, and generates a weekly digest with a verdict per video:
"watch fully / a summary is enough / skip".

## Structure
```
videos.txt              input: one video URL per line, # = comment
data/raw/               raw subtitle files, json3 (git-ignored)
data/transcripts/       <video_id>.md: frontmatter + time chunks (git-ignored)
data/db.sqlite          videos + analyses, the LLM cache (git-ignored)
data/review_*.md        blind human review / model scores (export-review)
digests/                generated weekly digests
docker-compose.yml      Qdrant service (ports 6333/6334)
src/video_digest/
  config.py             settings from .env (paths, Qdrant URL, LLM config)
  cli.py                typer app, `uv run video-digest <command>`
  ingest.py             stage 1: videos.txt -> transcripts (yt-dlp)
  llm.py                llm(): the only way to call an LLM
  summarize.py          stage 2 (+ transcripts, schema, features, db, review .py)
  prompts/              summarize_v1.md (PROMPT_VERSION), glossary.txt
tests/                  pytest; fixtures/fakes only, never real network
```

## Rules
- Python 3.12, managed with uv (`uv add`, `uv run`). No pip.
- Type hints everywhere. Pydantic models for all schemas and LLM outputs.
- Every LLM call goes through one function `llm()` in
  `src/video_digest/llm.py`, using the OpenAI-compatible `openai` client.
  Base URL, API key and model come from `config.py`
  (`LLM_MODEL_FAST` for bulk summaries, `LLM_MODEL_SMART` for cluster
  naming and the digest). Never hardcode models or URLs.
- Cache LLM results by sha256(transcript + PROMPT_VERSION + model) in db.sqlite;
  never re-call for a cached key. A prompt change = new file + PROMPT_VERSION.
- Summaries and other prose in Russian; topic tags in English (shared vocabulary).
- demo_value comes from text hints only (no video frames) and is flagged by
  demo_confidence; never present it as certain.
- Tests never call real APIs: fake clients only.
- Settings only via `config.py`; never crash on missing keys at import.
- YouTube: never request translated auto-captions (HTTP 429); one own-language
  track per video. All yt-dlp calls go through `make_ydl()` in ingest.py.
- Never commit `.env`, transcripts, the database or Qdrant data (data/qdrant/).

## Stages
Work strictly one stage at a time. Never build ahead of the current stage:
no scripts, modules or dependencies for future stages until asked.
Current stage: 2 (per-video summaries and scores).
