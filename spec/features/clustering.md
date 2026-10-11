---
status: planned
stage: 4
code: []
---
# Clustering

## Goal
Group videos into themes and state what the videos in a theme agree and disagree on.

## How it works
1. UMAP + HDBSCAN through BERTopic on the stage 3 vectors.
2. The noise label (-1) means "unlike the rest", not "bad".
3. `LLM_MODEL_SMART` names each cluster and lists common theses and contradictions, from stored
   analyses only.

## Input and output
Vectors + analyses -> clusters with names, theses, contradictions.

## CLI commands
Planned (stage 4 command name to be decided).

## Settings (.env)
`LLM_MODEL_SMART`, `LLM_REASONING_EFFORT_SMART`.

## Data
New tables to be defined in data-contracts.md first.

## Edge cases and errors
- Meaningful only from about 50-100 videos; below that, report topics instead.

## Done criteria
To be defined with the stage.

## Open questions
- Relation between clusters and `topics.txt`.
