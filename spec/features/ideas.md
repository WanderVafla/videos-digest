---
status: planned
stage: 7
code: []
---
# Optional ideas (stage 7)

Short notes; each becomes its own feature file when started.

## visual-demo-check
Check ambiguous `demo_value` / `demo_confidence = low` cases with a multimodal model on a few
frames. Only for videos where the verdict depends on it.

## whisper-fallback
Transcribe videos without usable captions (`transcript_status: no_transcript`) with Whisper.
Currently 0 of 41 transcripts need it.

## html-report
An HTML version of the digest for reading in a browser.

## auto-ingest
Add new videos from chosen channels or playlists automatically (respecting decisions D1-D2).

## mcp-server
Expose search and analyses to agents over MCP.

## jev-classifier-experiment
An experiment with the Jev classifier after calibration; decide if it is worth it
(status.md, open questions).
