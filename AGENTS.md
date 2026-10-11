# AGENTS.md

video-digest turns YouTube videos about neural networks (Russian and English) into a weekly
digest: summary, scores, topics and a verdict per video (watch fully / summary is enough / skip).

## Safety rules
- Never make real requests to YouTube (`ingest`, yt-dlp) unless the user asks:
  the user's IP was rate-limited by YouTube once.
- Never call paid LLM APIs (`summarize`, translating `export-review`, anything using `llm()`)
  unless the user asks: it costs money. Tests use fakes only, never the network.
- Never print or commit API keys or the contents of `.env`.
- Do not push and do not merge; the user merges (squash PRs).
- One stage at a time (spec/pipeline.md); never build ahead of the current stage.

## Commands
```
uv sync                                               install dependencies
uv run pytest                                         offline tests
uvx pyright --pythonpath .venv/bin/python src tests   type check
uv run video-digest ingest                            videos.txt -> transcripts (YouTube!)
uv run video-digest summarize                         LLM analysis per transcript (paid)
uv run video-digest export-review                     review files (paid if it translates)
uv run video-digest calibrate                         blind ratings vs the model
uv run video-digest topics                            topic slugs with usage counts
uv run video-digest topics merge OLD NEW              merge topic OLD into NEW
```

## What to read when
- Every session: spec/status.md, then spec/pipeline.md.
- Before changing ANY file: find it in the code map in spec/README.md and read the spec
  files listed there, including "Working on this code".
- A new file under src/, tests/ or spec/ gets a row in the code map in the same commit, no
  question needed. A file anywhere else, or an existing tracked file missing from the map:
  ask first.
- Something looks odd or over-built: spec/decisions.md (Pitfalls at the end).
- Prompts, rubric, glossary: src/video_digest/prompts/. Topics: topics.txt.

## Sync rule
- Any change in behavior updates the matching spec file in the same commit.
- A changed data format updates spec/data-contracts.md first, then the code.
- Spec and code disagree = bug: fix one of them in the same commit.
- User-facing features are also documented in README.md.

## Code rules
- Python 3.12 + uv, type hints everywhere, pydantic for schemas, settings only via config.py.
- All LLM calls go through `llm()` (src/video_digest/llm.py); all yt-dlp calls through
  `make_ydl()` (src/video_digest/ingest.py).

Details: spec/README.md.
