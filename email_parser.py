"""Raw Gmail message dict -> the fields we care about.

The only module that knows Gmail's JSON shape. Step 4.
"""


def parse(message: dict) -> dict:
    """Return {recipient_name, recipient_email, subject, sent_date, body}."""
    raise NotImplementedError("step 4")
