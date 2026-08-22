"""Raw Gmail message dict -> the fields we care about.

The only module that knows Gmail's JSON shape.
"""

import base64
import binascii
import logging
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime

from bs4 import BeautifulSoup

import config

log = logging.getLogger(__name__)

# "On Mon, 23 Jun 2026 at 09:12, Someone <x@y.com> wrote:" and friends.
QUOTE_MARKERS = (
    re.compile(r"^\s*On .{0,120}wrote:\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*_{5,}\s*$"),
    re.compile(r"^\s*From:\s.+", re.IGNORECASE),
)


def _headers(message: dict) -> dict:
    """Lower-cased header name -> value."""
    return {
        h["name"].lower(): h["value"]
        for h in message.get("payload", {}).get("headers", [])
    }


def _decode_header(value: str) -> str:
    """Decode RFC-2047 encoded words, e.g. =?UTF-8?B?4KSo?= -> readable text."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, ValueError, LookupError) as exc:
        log.warning("Could not decode header %r: %s", value[:40], exc)
        return value


def _recipient(to_header: str) -> tuple[str, str]:
    """(name, email) of the first real address in a To header.

    getaddresses handles quoted names containing commas, which a naive split does not.
    """
    for name, address in getaddresses([to_header or ""]):
        if address:
            return _decode_header(name).strip().strip('"'), address.strip().lower()
    return "", ""


def _sent_date(message: dict, headers: dict) -> str:
    """Sent date as YYYY-MM-DD, from the Date header or Gmail's internalDate."""
    raw = headers.get("date", "")
    if raw:
        try:
            return parsedate_to_datetime(raw).date().isoformat()
        except (TypeError, ValueError):
            log.warning("Unparseable Date header %r, using internalDate", raw)

    internal = message.get("internalDate")
    if internal:
        moment = datetime.fromtimestamp(int(internal) / 1000, tz=timezone.utc)
        return moment.date().isoformat()
    return ""


def _decode_body(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError) as exc:
        log.warning("Could not decode a body part: %s", exc)
        return ""


def _collect_parts(payload: dict, plain: list, html: list) -> None:
    """Walk the MIME tree, gathering text/plain and text/html bodies."""
    data = payload.get("body", {}).get("data")
    if data:
        mime = payload.get("mimeType", "")
        if mime == "text/plain":
            plain.append(_decode_body(data))
        elif mime == "text/html":
            html.append(_decode_body(data))
    for part in payload.get("parts", []):
        _collect_parts(part, plain, html)


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n")


def _tidy(text: str) -> str:
    """Trim trailing spaces and collapse runs of blank lines."""
    lines = [line.rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _body_text(message: dict) -> str:
    """Body as plain text, preferring text/plain and falling back to HTML."""
    plain, html = [], []
    _collect_parts(message.get("payload", {}), plain, html)
    if any(p.strip() for p in plain):
        return _tidy("\n".join(plain))
    if html:
        return _tidy(_html_to_text("\n".join(html)))
    return ""


def _split_quoted(body: str) -> tuple[str, str]:
    """(my text, the quoted reply below it). Either half may be empty."""
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if any(marker.match(line) for marker in QUOTE_MARKERS):
            return _tidy("\n".join(lines[:index])), _tidy("\n".join(lines[index:]))

    # No marker, but "> " prefixed lines still mark a quote.
    for index, line in enumerate(lines):
        if line.startswith(">"):
            return _tidy("\n".join(lines[:index])), _tidy("\n".join(lines[index:]))
    return _tidy(body), ""


def parse(message: dict) -> dict:
    """Return the fields we care about, plus the quoted reply as a bonus signal."""
    headers = _headers(message)
    name, address = _recipient(headers.get("to", ""))
    body, quoted = _split_quoted(_body_text(message))

    return {
        "message_id": message.get("id", ""),
        "recipient_name": name,
        "recipient_email": address,
        "subject": _decode_header(headers.get("subject", "")),
        "sent_date": _sent_date(message, headers),
        "body": body,
        "quoted_body": quoted,
    }


if __name__ == "__main__":
    # Parse check: writes the parsed fields of the fetched messages to a CSV.
    import gmail_client
    import storage

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    rows = []
    for message in gmail_client.fetch_sent_messages(gmail_client.get_service()):
        parsed = parse(message)
        preview = " ".join(parsed["body"].split())[:200]
        rows.append(
            {
                **parsed,
                "body_chars": len(parsed["body"]),
                "quoted_chars": len(parsed["quoted_body"]),
                "body_preview": preview,
            }
        )

    out = storage.write_csv(
        rows,
        config.OUTPUT_PATH.with_name("step4_parsed.csv"),
        [
            "message_id",
            "recipient_name",
            "recipient_email",
            "subject",
            "sent_date",
            "body_chars",
            "quoted_chars",
            "body_preview",
        ],
    )
    print(f"\nParsed {len(rows)} messages -> {out}")
