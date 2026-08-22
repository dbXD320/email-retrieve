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
EXPERIENCE_PATH = _path("EXPERIENCE_PATH", "output/experience.csv")
SEARCH_CACHE_PATH = _path("SEARCH_CACHE_PATH", "output/search_cache.json")

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

# Experience lookup: set to false to stop the app fetching public web pages.
ENABLE_EXPERIENCE_SEARCH = os.getenv("ENABLE_EXPERIENCE_SEARCH", "true").strip().lower() in {
    "1",
    "true",
    "yes",
}

# Search providers, tried in this order. Only duckduckgo needs no credentials;
# the others switch on once their key or url is set below.
SEARCH_PROVIDERS = [
    name.strip()
    for name in os.getenv("SEARCH_PROVIDERS", "duckduckgo,google_cse,brave,searx").split(",")
    if name.strip()
]

# How long to leave a provider alone after it refuses us.
SEARCH_COOLDOWN_SECONDS = _int("SEARCH_COOLDOWN_SECONDS", 900)

# How long a cached search result stays usable.
SEARCH_CACHE_DAYS = _int("SEARCH_CACHE_DAYS", 7)

# Optional provider credentials. Each one enables its provider.
GOOGLE_CSE_KEY = os.getenv("GOOGLE_CSE_KEY", "").strip()
GOOGLE_CSE_CX = os.getenv("GOOGLE_CSE_CX", "").strip()
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
SEARX_URL = os.getenv("SEARX_URL", "").strip()
