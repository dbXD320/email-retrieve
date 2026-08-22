"""Gmail API access: OAuth login and fetching sent messages.

The only module that talks to Google. Step 2 fills in auth, step 3 the fetching.
"""


def get_service():
    """Authenticate and return a Gmail API service object.

    Uses the cached token at config.TOKEN_PATH, refreshing it when expired and
    falling back to the browser consent flow when there is no usable token.
    """
    raise NotImplementedError("step 2")


def fetch_sent_messages(service):
    """Yield raw Gmail message dicts for everything in `in:sent`, newest first.

    Follows pagination and honours config.MAX_MESSAGES and config.AFTER_DATE.
    """
    raise NotImplementedError("step 3")
