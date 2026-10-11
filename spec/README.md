# Specification

The spec says how the system should work; the code implements it. A mismatch between them is a
bug: fix one of the two in the same commit. Statements here are checked against the code.

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
## Open questions
```

## When to update what

| Change | Update |
|---|---|
| Behavior of a feature | its features/*.md (same commit) |
| File format, table, JSON field, cache key, setting | data-contracts.md first, then code |
| A choice someone might later undo | append to decisions.md |
| Stage finished, task done, new question | status.md |
| New stage or feature | pipeline.md and a new features/*.md |

## Not stored in the spec

Copies drift apart, so these are only linked:
- prompt texts, scoring rubric, verdict rules: `src/video_digest/prompts/`
- glossary of canonical names: `src/video_digest/prompts/glossary.txt`
- topic list: `topics.txt`
- settings with defaults: `.env.example` and `src/video_digest/config.py`
- user documentation: `README.md`
