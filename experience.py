"""Previous work experience per person, cached in its own CSV.

`output/experience.csv` holds one row per company a person worked at, in order.
The search itself is not built yet - `find` is the seam for it. Everything around
it (cache, ordering, storage) is done, so implementing `find` is all that is left.
"""

import datetime
import logging

import config
import storage

log = logging.getLogger(__name__)


def cached(email: str) -> list:
    """This person's stored experience, most recent first.

    Returns [] if they have never been searched. A single row with an empty
    company means they were searched and nothing was found - use `searched` to
    tell that apart from "never tried".
    """
    email = (email or "").strip().lower()
    if not email:
        return []

    rows = [
        row
        for row in storage.read_rows(config.EXPERIENCE_PATH)
        if (row.get("person_email") or "").strip().lower() == email
    ]
    rows.sort(key=lambda r: int(r.get("position") or 0))
    return [r for r in rows if (r.get("company") or "").strip()]


def searched(email: str) -> bool:
    """True if this person has been looked up before, hit or miss."""
    email = (email or "").strip().lower()
    return any(
        (row.get("person_email") or "").strip().lower() == email
        for row in storage.read_rows(config.EXPERIENCE_PATH)
    )


def save(person: dict, entries: list) -> None:
    """Store a person's experience. An empty `entries` records the miss.

    `entries` is a list of {"company", "role", "source"} in order, most recent
    previous role first.
    """
    stamp = datetime.datetime.now().strftime("%Y-%m-%d")
    common = {
        "person_email": (person.get("email") or "").strip().lower(),
        "person_name": person.get("name") or "",
        "current_company": person.get("company") or "",
        "found_at": stamp,
    }

    if not entries:
        # One blank row so this person is not searched again.
        rows = [{**common, "position": 0, "company": "", "role": "", "source": ""}]
    else:
        rows = [
            {
                **common,
                "position": index,
                "company": entry.get("company") or "",
                "role": entry.get("role") or "",
                "source": entry.get("source") or "",
            }
            for index, entry in enumerate(entries, start=1)
        ]

    storage.append_csv(rows, config.EXPERIENCE_PATH, storage.EXPERIENCE_FIELDNAMES)


def find(person: dict) -> list:
    """Search public sources for this person's previous roles.

    Not built yet. When it is, it should return a list of
    {"company", "role", "source"} in order, most recent previous role first, and
    the caller stores it with `save`.
    """
    raise NotImplementedError("web search for previous experience is not built yet")


def lookup(person: dict) -> tuple[list, bool]:
    """(entries, from_cache). Falls back to the cache when `find` is unavailable."""
    email = person.get("email") or ""
    if searched(email):
        return cached(email), True

    try:
        entries = find(person)
    except NotImplementedError:
        log.info("Experience lookup for %s skipped - search not built yet", email)
        return [], False

    save(person, entries)
    return entries, False
