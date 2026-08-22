"""Settings loaded from .env — the one place to change how the script behaves."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _path(name: str, default: str) -> Path:
    """Env path, resolved relative to the project root unless already absolute."""
    value = os.getenv(name, "").strip() or default
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


CLIENT_SECRET_PATH = _path("CLIENT_SECRET_PATH", "credentials/client_secret.json")
TOKEN_PATH = _path("TOKEN_PATH", "credentials/token.json")
OUTPUT_PATH = _path("OUTPUT_PATH", "output/sent_emails.csv")

# Read-only is all this script needs. Changing this invalidates the cached token.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# 0 means no limit. Keep it small while developing.
MAX_MESSAGES = _int("MAX_MESSAGES", 5)

# Optional Gmail date filter, YYYY/MM/DD. Empty means all sent mail.
AFTER_DATE = os.getenv("AFTER_DATE", "").strip()

# Company extraction: ask the LLM only when the text rules find nothing.
ENABLE_LLM_FALLBACK = os.getenv("ENABLE_LLM_FALLBACK", "true").strip().lower() in {
    "1",
    "true",
    "yes",
}
LLM_MODEL = os.getenv("LLM_MODEL", "").strip() or "gpt-4o-mini"
