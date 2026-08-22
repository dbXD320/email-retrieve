"""CSV output.

`write_csv` is generic so any step can dump what it has with proper headings;
`write_rows` is the final shape main.py writes.
"""

import csv
import logging
from pathlib import Path

log = logging.getLogger(__name__)

FIELDNAMES = [
    "recipient_name",
    "recipient_email",
    "subject",
    "sent_date",
    "company",
    "company_source",
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
