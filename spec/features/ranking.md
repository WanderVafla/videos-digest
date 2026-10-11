---
status: planned
stage: 4
code: []
---
# Ranking

## Goal
One final score per video and a verdict that the digest can rely on.

## How it works
1. Final score = 0.30 practical_value + 0.25 information_density + 0.25 novelty + 0.20 demo_value.
2. Novelty = 10 x (1 - max cosine similarity to earlier videos only).
3. Verdict thresholds: >= 7.5 watch fully, < 4 skip, otherwise summary is enough.
4. Duplicates: high similarity inside a cluster.

Weights and thresholds are starting points to be calibrated against the user's blind ratings
(features/calibration.md), not validated values.

## Input and output
Analyses + vectors + clusters -> final score, verdict, duplicate links.

## CLI commands
Planned.

## Settings (.env)
To be decided (weights may become settings).

## Data
To be defined in data-contracts.md first.

## Edge cases and errors
- The first video has no earlier videos: novelty definition needed.
- `demo_confidence = low` may need to down-weight demo_value.

## Done criteria
Calibration agreement with blind ratings above a threshold to be chosen.

## Open questions
- Use the LLM verdict, the computed verdict, or both?
