# Pipeline

Stages follow the development plan. Build strictly one stage at a time.

```
videos.txt
   │  1 ingest (yt-dlp, YouTube)
   ▼
data/transcripts/<id>.md  +  data/raw/<id>.<lang>.json3
   │  2 summarize (llm(), LLM_MODEL_FAST)          topics.txt ◄─► (grows)
   ▼
data/db.sqlite: videos, analyses ──► export-review ──► data/review_blind.md ──► calibrate
   │  3 embed (planned)
   ▼
Qdrant vectors ──► search
   │  4 analyze: clusters, duplicates, novelty, final score (planned)
   ▼
   │  5 digest (planned, LLM_MODEL_SMART, DIGEST_LANGUAGE)
   ▼
digests/<week>.md
```

| # | Stage | Input | Output | Command | Status | Features |
|---|---|---|---|---|---|---|
| 0 | Setup | – | uv project, config, folders | `uv sync` | done | – |
| 1 | Ingest | `videos.txt` | `data/transcripts/*.md`, `data/raw/*.json3` | `video-digest ingest` | done | [ingest](features/ingest.md) |
| 2 | Summaries and scores | transcripts | `analyses` rows, `topics.txt`, review files | `video-digest summarize`, `export-review`, `calibrate`, `topics` | done | [analysis](features/analysis.md), [llm-client](features/llm-client.md), [caching](features/caching.md), [topics](features/topics.md), [languages](features/languages.md), [translation](features/translation.md), [demo-hints](features/demo-hints.md), [review-export](features/review-export.md), [calibration](features/calibration.md) |
| 3 | Embeddings and search | transcripts, analyses | vectors in Qdrant | planned (`search`) | planned | [embeddings-search](features/embeddings-search.md) |
| 4 | Analysis | vectors, analyses | clusters, duplicates, novelty, final score | planned | planned | [clustering](features/clustering.md), [ranking](features/ranking.md) |
| 5 | Digest | stage 2-4 data | `digests/*.md` | planned | planned | [digest](features/digest.md) |
| 6 | Agent integration | all data | AGENTS.md, CLI commands for interactive questions | planned | planned | – (AGENTS.md exists) |
| 7 | Optional | – | visual demo check, HTML report, Whisper, auto-ingest, MCP server | – | planned | [ideas](features/ideas.md) |

Notes
- Stage 2 also covers language settings, the dynamic topic list and the blind review loop.
- Already present for later stages: `QDRANT_URL`, `LLM_MODEL_SMART`, `docker-compose.yml`
  (Qdrant on ports 6333/6334), `digests/`. Nothing reads them yet.
