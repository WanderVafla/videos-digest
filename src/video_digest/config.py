"""Project settings loaded from environment / .env. Missing keys never raise."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR: Path = Path(__file__).resolve().parents[2]

load_dotenv(ROOT_DIR / ".env")

DATA_DIR: Path = ROOT_DIR / "data"
TRANSCRIPTS_DIR: Path = DATA_DIR / "transcripts"
RAW_DIR: Path = DATA_DIR / "raw"  # raw subtitle files (json3)
DIGESTS_DIR: Path = ROOT_DIR / "digests"
DB_PATH: Path = DATA_DIR / "db.sqlite"
VIDEOS_FILE: Path = ROOT_DIR / "videos.txt"
TOPICS_FILE: Path = ROOT_DIR / "topics.txt"  # topic taxonomy, grows during summarize

QDRANT_URL: str = os.getenv("QDRANT_URL", "http://localhost:6333")

LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
LLM_MODEL_FAST: str = os.getenv("LLM_MODEL_FAST", "")  # bulk summaries
LLM_MODEL_SMART: str = os.getenv("LLM_MODEL_SMART", "")  # cluster naming, digest
# Reasoning effort per model tier (OpenRouter `reasoning.effort`; reasoning tokens bill as output).
# Empty = do not send the parameter.
LLM_REASONING_EFFORT_FAST: str = os.getenv("LLM_REASONING_EFFORT_FAST", "low")
LLM_REASONING_EFFORT_SMART: str = os.getenv("LLM_REASONING_EFFORT_SMART", "medium")
# max_tokens for every LLM call; reasoning tokens count as output. Without it OpenRouter reserves
# credit for the model's maximum output (65,536) and answers 402 on a low balance.
# Default: 3x the largest stored tokens_out (4622), rounded up to 1000 (spec/decisions.md). 0 = not sent.
LLM_MAX_OUTPUT_TOKENS: int = int(os.getenv("LLM_MAX_OUTPUT_TOKENS") or 14000)

# Language of generated and stored analyses: ISO 639-1 code ("en", "ru", ...) or "source" (video's language).
ANALYSIS_LANGUAGE: str = os.getenv("ANALYSIS_LANGUAGE", "en").strip().lower()
# Language shown when reading (review files, later the digest): ISO 639-1 code.
DIGEST_LANGUAGE: str = os.getenv("DIGEST_LANGUAGE", "en").strip().lower()

# JS runtime for yt-dlp YouTube extraction: deno, node, bun or quickjs (empty = yt-dlp default, deno)
YTDLP_JS_RUNTIME: str = os.getenv("YTDLP_JS_RUNTIME", "bun")
