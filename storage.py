"""CSV output. Step 6."""

FIELDNAMES = [
    "recipient_name",
    "recipient_email",
    "subject",
    "sent_date",
    "company",
    "company_source",
]


def write_rows(rows, path):
    """Write rows to `path` as CSV with FIELDNAMES as the header."""
    raise NotImplementedError("step 6")
