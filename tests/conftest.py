"""Shared test helpers.

Everything here is synthetic and offline: no Google credentials, no network.
The real payloads under tests/fixtures/ are gitignored (they contain real
contacts), so tests that want them skip when they are missing.
"""

import base64
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def encode(text: str) -> str:
    """Base64url, the way Gmail encodes body parts."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def part(mime: str, text: str) -> dict:
    """One leaf MIME part carrying text."""
    return {"mimeType": mime, "body": {"data": encode(text), "size": len(text)}}


def attachment(mime: str = "application/pdf") -> dict:
    """A part with no decodable text, e.g. a resume."""
    return {"mimeType": mime, "body": {"size": 12345}}


def multipart(mime: str, *parts) -> dict:
    return {"mimeType": mime, "body": {"size": 0}, "parts": list(parts)}


def message(
    payload: dict,
    to: str = "Test Person <person@acme.com>",
    subject: str = "Hello",
    date: str = "Thu, 25 Jun 2026 16:04:07 +0530",
    msg_id: str = "m1",
    thread_id: str = "t1",
    internal_date: str = "1782000000000",
    extra_headers=None,
) -> dict:
    """A Gmail messages.get(format='full') response, trimmed to what we read."""
    headers = [
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": date},
    ]
    headers += extra_headers or []
    payload = {**payload, "headers": headers}
    return {
        "id": msg_id,
        "threadId": thread_id,
        "internalDate": internal_date,
        "payload": payload,
    }


def simple_message(body_text: str, **kwargs) -> dict:
    """The common case: a single text/plain body."""
    return message(part("text/plain", body_text), **kwargs)


@pytest.fixture
def real_payloads():
    """The saved real Gmail payloads, or skip if they were never fetched."""
    files = sorted(FIXTURES.glob("*.json"))
    if not files:
        pytest.skip("no saved payloads in tests/fixtures/")
    return {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in files}


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """Never call a real API from the test suite.

    Autouse, so a test that wants the LLM path must opt in explicitly by
    monkeypatching company._ask itself.
    """
    import config

    monkeypatch.setattr(config, "ENABLE_LLM_FALLBACK", False)
