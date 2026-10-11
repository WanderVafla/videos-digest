"""Project settings loaded from environment / .env. Missing keys never raise."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR: Path = Path(__file__).resolve().parents[2]

load_dotenv(ROOT_DIR / ".env")

DATA_DIR: Path = ROOT_DIR / "data"
TRANSCRIPTS_DIR: Path = DATA_DIR / "transcripts"
DIGESTS_DIR: Path = ROOT_DIR / "digests"
DB_PATH: Path = DATA_DIR / "db.sqlite"
VIDEOS_FILE: Path = ROOT_DIR / "videos.txt"

QDRANT_URL: str = os.getenv("QDRANT_URL", "http://localhost:6333")

LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
LLM_MODEL_FAST: str = os.getenv("LLM_MODEL_FAST", "")  # bulk summaries
LLM_MODEL_SMART: str = os.getenv("LLM_MODEL_SMART", "")  # cluster naming, digest
