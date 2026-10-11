# Overview

## Goal

Save the time spent watching videos about neural networks and AI tools:
- find the common themes and theses across many videos;
- rank the videos that are worth watching fully;
- summarize the ones that are not, so reading the summary is enough.

## Non-goals

- No graph RAG, unless questions about relationships between entities actually come up.
- No general chat product; the interface is a CLI plus an interactive coding agent.
- No embedding of raw video or audio; only text (transcripts and analyses) is processed.

## User context

- A single user, running everything locally.
- Videos in Russian and English (other languages work but are rare).
- The interactive agent is Claude Code or opencode; it reads `AGENTS.md` and this spec.
- LLM provider at the moment: OpenRouter (any OpenAI-compatible API works).

## Glossary

| Term | Meaning |
|---|---|
| transcript | `data/transcripts/<video_id>.md`: caption text of one video with frontmatter (stage 1) |
| chunk | A fixed time window of a transcript (default 120 s) with a heading `## [HH:MM:SS - HH:MM:SS]` |
| analysis | The LLM result for one transcript: summary, key points, topics, scores, verdict (`VideoAnalysis`) |
| verdict | `watch_full`, `summary_enough` or `skip` |
| demo hints | Text-only signals computed in code (pointer phrases, commands, long pauses, title keywords) that help estimate `demo_value` |
| topic | A `{slug, description}` tag; slugs are lowercase English kebab-case |
| taxonomy | The list of all topics, stored in `topics.txt`; grows during `summarize` |
| analysis language | `ANALYSIS_LANGUAGE`: language analyses are generated and stored in (part of the cache key) |
| digest language | `DIGEST_LANGUAGE`: language the user reads in; text is translated at display time |
| blind review | The user rates videos in `data/review_blind.md` without seeing model scores; `calibrate` compares |
