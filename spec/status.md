# Status

**Current stage:** 2 (summaries and scores) merged into `main`. Next: finish stage 2 calibration,
then stage 3.

## Done
- Stage 0: uv project, config, folders, docker-compose for Qdrant.
- Stage 1: `ingest` (original-language captions, random delays, stop on 429).
- Stage 2: `summarize`, `export-review`, `calibrate`, `topics`, `topics merge`; language settings;
  dynamic topic list; display translation; demo hints.
- Local data: 41 transcripts; 8 videos analyzed with `summarize_v2` on
  `anthropic/claude-haiku-5.5` (~$0.003 per video); `topics.txt` has 14 topics.

## Next tasks (in order)
1. Set the OpenRouter key and models in `.env`.
2. Run `summarize --limit 5`.
3. Compare two fast models on the same videos, e.g. `anthropic/claude-haiku-5.5` and
   `openai/gpt-6-luna` (`summarize --force` with another `LLM_MODEL_FAST`; `calibrate --model`).
4. Check numbers in summaries against the transcripts.
5. Run `summarize` on all videos.
6. Rate 15-20 videos blind in `data/review_blind.md` (`export-review` first).
7. Run `calibrate`.
8. Start stage 3 (features/embeddings-search.md).

## Open questions
- Which fast model to keep (after step 3)?
- Calibration thresholds: what verdict agreement / score MAE is "good enough"?
- Is the Jev classifier worth an experiment after calibration?
- Is Whisper needed for videos without captions? Currently 0 of 41 transcripts are `no_transcript`.
- No `max_tokens` is sent; OpenRouter answered 402 when the balance could not cover the model's
  maximum output (65,536 tokens). Add a limit?
- A 401/403 does not stop `summarize`; every remaining video fails the same way. Stop early?
- `export-review` uses the newest analysis per video from any model, `calibrate` filters by
  model: after a model comparison the blind file may show another model's summaries.
- Analyses in different languages coexist; readers take the newest row per video regardless
  of language.
- `topics merge` reads all analysis rows; old `summarize_v1` rows store topics as strings and
  will likely make it fail. Delete v1 rows or skip them in code?
- `title_keywords` uses only the title: the video description is not ingested.
- The summary length check is loose (3-10 sentences) while the prompt asks for 5-7.
- Current topics include narrow or overlapping slugs (`mcp-protocol`, `local-llm-tools`,
  `cnn-kernel-size`); clean up with `topics merge`?
- Claude Haiku 5.5 long-prompt pricing (> 100k tokens) and `openai/gpt-6-luna` were not
  verified here.
