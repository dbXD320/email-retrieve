"""CSV output.

`write_csv` is generic so any step can dump what it has with proper headings;
`write_rows` is the final shape main.py writes.
"""

import csv
import logging
from pathlib import Path

log = logging.getLogger(__name__)

FIELDNAMES = [
    # ids first: they let the web app re-fetch one message to fill a gap
    "message_id",
    "thread_id",
    "recipient_name",
    "recipient_email",
    "subject",
    "sent_date",
    "company",
    "company_source",
]


# One row per company a person worked at. `position` is the order, 1 = most
# recent previous role, counting backwards. A single row with an empty `company`
# means "searched, found nothing" so the search is not repeated.
EXPERIENCE_FIELDNAMES = [
    "person_email",
    "person_name",
    "current_company",
    "position",
    "company",
    "role",
    "dates",
    "source",
    "found_at",
]


def write_csv(rows, path, fieldnames=None) -> Path:
    """Write dict rows to `path` as CSV, creating the parent directory."""
    path = Path(path)
    fieldnames = fieldnames or FIELDNAMES
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel on Windows shows accented names correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        count = 0
        for row in rows:
            writer.writerow(row)
            count += 1
    log.info("Wrote %s rows to %s", count, path)
    return path


def write_rows(rows, path) -> Path:
    """Write the final results using the standard headings."""
    return write_csv(rows, path, FIELDNAMES)


def read_rows(path) -> list:
    """Read back a CSV written by write_rows. Empty list if it is not there yet."""
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def append_csv(rows, path, fieldnames) -> Path:
    """Append rows, writing the header first if the file is new."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)
    log.info("Appended %s rows to %s", len(rows), path)
    return path
