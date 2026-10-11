# Specification

The spec says how the system should work; the code implements it. A mismatch between them is a
bug: fix one of the two in the same commit. Statements here are checked against the code.

## Code map: read before you change

Before changing a file, read the spec files listed for it. Feature files end with
"Working on this code": invariants, tests to run, traps.

A new file under src/, tests/ or spec/ gets a row in the code map in the same commit, no
question needed. A file anywhere else, or an existing tracked file missing from the map: ask first.
`tests/test_spec_map.py` fails when a file from `git ls-files` has no row (exact path, or a
directory row ending in `/`) or a row names a missing path.

| Code | Read first |
|---|---|
| `src/video_digest/ingest.py`, `videos.example.txt` | [features/ingest.md](features/ingest.md), decisions D1-D3, D12 |
| `src/video_digest/transcripts.py` | [data-contracts.md](data-contracts.md) (Transcript), [features/analysis.md](features/analysis.md) |
| `src/video_digest/summarize.py` | [features/analysis.md](features/analysis.md), [features/caching.md](features/caching.md) |
| `src/video_digest/schema.py` | [data-contracts.md](data-contracts.md) (Analysis JSON), [features/analysis.md](features/analysis.md) |
| `src/video_digest/llm.py` | [features/llm-client.md](features/llm-client.md), decision D4 |
| `src/video_digest/features.py` | [features/demo-hints.md](features/demo-hints.md), decision D10 |
| `src/video_digest/topics.py`, `topics.txt` | [features/topics.md](features/topics.md), decision D9 |
| `src/video_digest/languages.py` | [features/languages.md](features/languages.md), decisions D6-D8 |
| `src/video_digest/translate.py` | [features/translation.md](features/translation.md) |
| `src/video_digest/review.py` | [features/review-export.md](features/review-export.md), [features/calibration.md](features/calibration.md) |
| `src/video_digest/db.py` | [data-contracts.md](data-contracts.md) (SQLite, Cache keys), [features/caching.md](features/caching.md) |
| `src/video_digest/config.py`, `.env.example` | [data-contracts.md](data-contracts.md) (Settings) |
| `src/video_digest/cli.py`, `src/video_digest/__init__.py` | the feature file of the command + root `README.md` (user docs) |
| `src/video_digest/prompts/summarize_v2.md`, `src/video_digest/prompts/__init__.py` | [features/analysis.md](features/analysis.md), decision D5 (new version, never edit in place) |
| `src/video_digest/prompts/translate_v1.md` | [features/translation.md](features/translation.md) |
| `src/video_digest/prompts/glossary.txt` | [features/languages.md](features/languages.md) |
| `tests/` | the feature file of the module under test (Done criteria) |
| `tests/test_cli.py` | the feature file of the command (analysis.md, review-export.md) |
| `tests/test_spec_map.py` | this file (code map), `AGENTS.md` (links) |
| `spec/` | this file ("When to update what"); every link must resolve (`tests/test_spec_map.py`) |
| `AGENTS.md` | this file; keep its commands in sync with `src/video_digest/cli.py` |
| `README.md` | the feature file of the documented command (user docs) |
| `pyproject.toml`, `uv.lock`, `.python-version` | `AGENTS.md` (code rules); new dependencies only for the current stage |
| `docker-compose.yml` | [features/embeddings-search.md](features/embeddings-search.md) (stage 3, planned) |
| `digests/` | [features/digest.md](features/digest.md) (stage 5, planned) |
| `.gitignore` | [data-contracts.md](data-contracts.md) ("in git" notes), `AGENTS.md` safety rules |

## Files

| File | Read when |
|---|---|
| [status.md](status.md) | Every session first: current stage, next tasks, open questions |
| [pipeline.md](pipeline.md) | Every session: stages 0-7, data flow, which feature belongs where |
| [overview.md](overview.md) | Goal, non-goals, user context, glossary of terms |
| [data-contracts.md](data-contracts.md) | Touching any file format, table, JSON field, cache key or setting |
| [decisions.md](decisions.md) | Something looks odd; before "simplifying" it. Ends with Pitfalls |
| [features/](features/) | Working on one feature: one file per feature |

## Reading order for a new session

1. status.md
2. pipeline.md
3. the relevant features/*.md and data-contracts.md
4. decisions.md if the task touches something that looks odd

## Feature status

- `planned`: designed, no code yet; details may change.
- `in-progress`: partly implemented; the file says what is missing.
- `done`: implemented, tested, verified against the code.

## Feature file template

```markdown
---
status: planned | in-progress | done
stage: N
code: [src/video_digest/<module>.py, ...]
---
# <Feature>

## Goal            1-2 sentences
## How it works    numbered steps
## Input and output
## CLI commands
## Settings (.env)
## Data            tables, files
## Edge cases and errors
## Done criteria   how to verify
## Working on this code   invariants, tests to run, traps (done features)
## Open questions
```

## When to update what

| Change | Update |
|---|---|
| Behavior of a feature | its features/*.md (same commit) |
| File format, table, JSON field, cache key, setting | data-contracts.md first, then code |
| A choice someone might later undo | append to decisions.md |
| Stage finished, task done, new question | status.md |
| New stage or feature | pipeline.md, a new features/*.md and a row in the code map above |
| New file under src/, tests/ or spec/ | a row in the code map above, same commit |

## Not stored in the spec

Copies drift apart, so these are only linked:
- prompt texts, scoring rubric, verdict rules: `src/video_digest/prompts/`
- glossary of canonical names: `src/video_digest/prompts/glossary.txt`
- topic list: `topics.txt`
- settings with defaults: `.env.example` and `src/video_digest/config.py`
- user documentation: `README.md`
