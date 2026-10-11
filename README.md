# video-digest

Processes transcripts of neural-network videos (RU/EN), summarizes and scores them
with an LLM, clusters topics and builds a weekly digest:
"watch fully / a summary is enough / skip".

## Setup

```bash
uv sync                        # create .venv and install dependencies
cp .env.example .env           # then fill in LLM_API_KEY and model names
docker compose up -d qdrant    # vector DB (needed from later stages)
```

Add video URLs to `videos.txt` (one per line, `#` = comment), then:

```bash
uv run video-digest ingest          # -> data/transcripts/<id>.md, existing files skipped
uv run video-digest summarize       # LLM summary + scores per video (cached; --limit N, --force, --id ID)
uv run video-digest export-review   # data/review_blind.md to fill in, data/review_model.md
uv run video-digest calibrate       # compare your ratings with the model
uv run pytest                       # offline tests
```

Project rules: `AGENTS.md`.
