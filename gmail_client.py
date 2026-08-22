"""Gmail API access: OAuth login and fetching sent messages.

The only module that talks to Google.
"""

import logging
import time

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config

log = logging.getLogger(__name__)

TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


# --- auth -------------------------------------------------------------------


def _load_token():
    """Return cached credentials, or None if there is no usable token file."""
    if not config.TOKEN_PATH.exists():
        return None
    try:
        return Credentials.from_authorized_user_file(str(config.TOKEN_PATH), config.SCOPES)
    except (ValueError, OSError) as exc:
        log.warning("Ignoring unreadable token %s: %s", config.TOKEN_PATH, exc)
        return None


def _save_token(creds) -> None:
    config.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    log.info("Token saved to %s", config.TOKEN_PATH)


def _run_consent_flow():
    """Open the browser for consent. Only needed when there is no valid token."""
    if not config.CLIENT_SECRET_PATH.exists():
        raise FileNotFoundError(
            f"No OAuth client secret at {config.CLIENT_SECRET_PATH}. Download a "
            "Desktop app client from Google Cloud Console and save it there."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(config.CLIENT_SECRET_PATH), config.SCOPES
    )
    log.info("Opening browser for Google consent.")
    return flow.run_local_server(port=0)


def get_credentials():
    """Valid credentials, from cache if possible, refreshing or re-consenting if not."""
    creds = _load_token()

    if creds and creds.valid:
        log.info("Using cached token.")
        return creds

    if creds and creds.expired and creds.refresh_token:
        log.info("Token expired, refreshing.")
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except RefreshError as exc:
            # Revoked, or the scopes changed - consent again rather than dying.
            log.warning("Refresh failed (%s), falling back to browser consent.", exc)

    creds = _run_consent_flow()
    _save_token(creds)
    return creds


def get_service():
    """Authenticated Gmail API service object."""
    return build("gmail", "v1", credentials=get_credentials(), cache_discovery=False)


# --- fetching ---------------------------------------------------------------


def _build_query() -> str:
    query = "in:sent"
    if config.AFTER_DATE:
        query += f" after:{config.AFTER_DATE}"
    return query


def _execute(request, attempts: int = 4):
    """Run an API request, backing off on rate limits and server errors."""
    for attempt in range(attempts):
        try:
            return request.execute()
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            if status not in TRANSIENT_STATUSES or attempt == attempts - 1:
                raise
            delay = 2**attempt
            log.warning("Gmail API returned %s, retrying in %ss", status, delay)
            time.sleep(delay)


def fetch_sent_messages(service):
    """Yield raw Gmail message dicts for everything in `in:sent`, newest first.

    Follows pagination and honours config.MAX_MESSAGES and config.AFTER_DATE.
    """
    query = _build_query()
    limit = config.MAX_MESSAGES or None
    page_size = min(100, limit) if limit else 100
    log.info("Fetching sent mail with query %r (limit: %s)", query, limit or "none")

    messages = service.users().messages()
    fetched = 0
    page_token = None

    while True:
        page = _execute(
            messages.list(userId="me", q=query, maxResults=page_size, pageToken=page_token)
        )
        for ref in page.get("messages", []):
            yield _execute(messages.get(userId="me", id=ref["id"], format="full"))
            fetched += 1
            if limit and fetched >= limit:
                log.info("Stopping at MAX_MESSAGES=%s", limit)
                return

        page_token = page.get("nextPageToken")
        if not page_token:
            log.info("No more pages, fetched %s messages", fetched)
            return


def _raw_headers(message: dict) -> dict:
    """Header values straight off the payload - a step 3 smoke check only.

    Real parsing (name/email split, body extraction) lives in email_parser.
    """
    headers = {
        h["name"].lower(): h["value"]
        for h in message.get("payload", {}).get("headers", [])
    }
    return {
        "message_id": message.get("id", ""),
        "thread_id": message.get("threadId", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
    }


if __name__ == "__main__":
    # Fetch check: writes the raw headers of the first messages to a CSV.
    import storage

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    service = get_service()
    profile = _execute(service.users().getProfile(userId="me"))
    print("\nAuthenticated as: " + profile["emailAddress"])

    rows = [_raw_headers(m) for m in fetch_sent_messages(service)]
    out = storage.write_csv(
        rows,
        config.OUTPUT_PATH.with_name("step3_sent_messages.csv"),
        ["message_id", "thread_id", "to", "subject", "date"],
    )
    print(f"Fetched {len(rows)} sent messages -> {out}")
