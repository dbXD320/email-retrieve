"""Gmail API access: OAuth login and fetching sent messages.

The only module that talks to Google.
"""

import logging

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config

log = logging.getLogger(__name__)


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
            # Revoked, or the scopes changed — consent again rather than dying.
            log.warning("Refresh failed (%s), falling back to browser consent.", exc)

    creds = _run_consent_flow()
    _save_token(creds)
    return creds


def get_service():
    """Authenticated Gmail API service object."""
    return build("gmail", "v1", credentials=get_credentials(), cache_discovery=False)


def fetch_sent_messages(service):
    """Yield raw Gmail message dicts for everything in `in:sent`, newest first.

    Follows pagination and honours config.MAX_MESSAGES and config.AFTER_DATE.
    """
    raise NotImplementedError("step 3")


if __name__ == "__main__":
    # Auth check: prints the account the token belongs to.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    profile = get_service().users().getProfile(userId="me").execute()
    print(f"\nAuthenticated as: {profile['emailAddress']}")
    print(f"Messages in mailbox: {profile['messagesTotal']}")
