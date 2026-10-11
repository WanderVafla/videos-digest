---
status: planned
stage: 3
code: []
---
# Embeddings and search

## Goal
Find videos and passages by meaning, across Russian and English.

## How it works
1. Embed the text only (transcript chunks and/or analysis text) with EmbeddingGemma 2, 768 dims.
2. Store vectors with video id, chunk start and language in Qdrant (`QDRANT_URL`,
   `docker-compose.yml`).
3. A `search` CLI command returns the closest videos/chunks with timestamps.

## Input and output
Transcripts and analyses -> Qdrant collection; query -> ranked hits.

## CLI commands
Planned: `video-digest embed`, `video-digest search "<query>"`.

## Settings (.env)
`QDRANT_URL`; embedding model settings to be added.

## Data
Qdrant storage in `data/qdrant/` (git-ignored); point ids and payload to be defined in
data-contracts.md first.

## Edge cases and errors
- Test Russian-English cross-language retrieval first (RU query -> EN video and back).
- Fallback if cross-language retrieval is weak: embed the summaries in the analysis language
  (English by default) instead of raw transcripts.

## Done criteria
Cross-language test set passes a recall threshold to be chosen; search works offline.

## Open questions
- Chunks, summaries, or both?
