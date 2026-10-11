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
data/db.sqlite          local state and LLM cache (git-ignored)
data/qdrant/            Qdrant storage (git-ignored)
digests/                generated weekly digests
docker-compose.yml      Qdrant service (ports 6333/6334)
src/video_digest/
  config.py             settings from .env (paths, Qdrant URL, LLM config)
  cli.py                typer app, `uv run video-digest <command>`
  ingest.py             stage 1: videos.txt -> transcripts (yt-dlp)
  llm.py                single llm() entry point (to be added)
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
- Cache LLM results keyed by a hash of the transcript (plus model/prompt);
  never re-call the LLM for an unchanged transcript.
- Settings are read only via `config.py`; never crash on missing keys at import.
- YouTube: never request translated auto-captions (HTTP 429); one own-language
  track per video. All yt-dlp calls go through `make_ydl()` in ingest.py.
- Never commit `.env`, transcripts, the database or Qdrant data.

## Stages
Work strictly one stage at a time. Never build ahead of the current stage:
no scripts, modules or dependencies for future stages until asked.
Current stage: 1 (transcript ingest).
