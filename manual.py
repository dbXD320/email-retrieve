"""Manually entered people: the same pipeline, a separate CSV.

Nothing about the search, extraction, caching or throttling is new here - it all
comes from `search` and `experience`. This module only adds a second store, so
people you type in by hand never mix with the Gmail-derived data, and an email
lookup for when you did not supply one.

`output/manual_lookups.csv` holds one row per company found, keyed by
name + current company (the email is optional, so it cannot be the key).
"""

import datetime
import logging

import config
import experience
import storage

log = logging.getLogger(__name__)

SearchUnavailable = experience.SearchUnavailable

FIELDNAMES = [
    "key",
    "person_name",
    "current_company",
    "email_given",
    "emails_found",
    "profile_url",
    "position",
    "company",
    "role",
    "dates",
    "source",
    "found_at",
]


def key_for(name: str, company: str) -> str:
    """Identity for a manual entry: name and current company, case-folded."""
    return f"{(name or '').strip().lower()}|{(company or '').strip().lower()}"


# --- the store ---------------------------------------------------------------


def _rows_for(key: str) -> list:
    return [
        row
        for row in storage.read_rows(config.MANUAL_PATH)
        if (row.get("key") or "") == key
    ]


def cached(name: str, company: str) -> list:
    """Stored experience for this person, most recent previous role first."""
    rows = _rows_for(key_for(name, company))
    rows.sort(key=lambda r: int(r.get("position") or 0))
    return [r for r in rows if (r.get("company") or "").strip()]


def searched(name: str, company: str) -> bool:
    """True if this name/company has been looked up before, hit or miss."""
    return bool(_rows_for(key_for(name, company)))


def details(name: str, company: str) -> dict:
    """The stored profile url and emails for this person, if any."""
    for row in _rows_for(key_for(name, company)):
        if row.get("profile_url") or row.get("emails_found"):
            return {
                "profile_url": (row.get("profile_url") or "").strip(),
                "emails": [e for e in (row.get("emails_found") or "").split(" ") if e],
                "email_given": (row.get("email_given") or "").strip(),
            }
    return {"profile_url": "", "emails": [], "email_given": ""}


def people() -> list:
    """One entry per person searched so far, newest first."""
    seen, out = set(), []
    for row in reversed(storage.read_rows(config.MANUAL_PATH)):
        key = row.get("key") or ""
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "key": key,
                "name": row.get("person_name") or "",
                "company": row.get("current_company") or "",
                "found_at": row.get("found_at") or "",
            }
        )
    return out


def forget(name: str, company: str) -> int:
    """Drop what is stored for this person so they can be searched again."""
    key = key_for(name, company)
    rows = storage.read_rows(config.MANUAL_PATH)
    keep = [r for r in rows if (r.get("key") or "") != key]

    removed = len(rows) - len(keep)
    if removed:
        storage.write_csv(keep, config.MANUAL_PATH, FIELDNAMES)
        log.info("Cleared %s manual rows for %r", removed, key)
    return removed


def save(person: dict, entries: list, profile: str, emails: list) -> None:
    """Store a manual lookup. An empty `entries` still records the attempt."""
    common = {
        "key": key_for(person.get("name", ""), person.get("company", "")),
        "person_name": person.get("name") or "",
        "current_company": person.get("company") or "",
        "email_given": (person.get("email") or "").strip().lower(),
        # space separated so the cell stays readable in a spreadsheet
        "emails_found": " ".join(e["email"] for e in emails),
        "profile_url": profile,
        "found_at": datetime.datetime.now().strftime("%Y-%m-%d"),
    }

    if not entries:
        rows = [{**common, "position": 0, "company": "", "role": "", "dates": "", "source": ""}]
    else:
        rows = [
            {
                **common,
                "position": index,
                "company": entry.get("company") or "",
                "role": entry.get("role") or "",
                "dates": entry.get("dates") or "",
                "source": entry.get("source") or "",
            }
            for index, entry in enumerate(entries, start=1)
        ]

    storage.append_csv(rows, config.MANUAL_PATH, FIELDNAMES)


# --- the lookup --------------------------------------------------------------


def lookup(name: str, company: str = "", email: str = "", refresh: bool = False) -> dict:
    """Run the experience pipeline for a typed-in person and store the result.

    Returns {entries, profile, emails, from_cache, error}. Old rows survive a
    failed search, exactly as the Gmail flow does.
    """
    person = {
        "name": (name or "").strip(),
        "company": (company or "").strip(),
        "email": (email or "").strip().lower(),
    }
    if not person["name"]:
        return {"entries": [], "profile": "", "emails": [], "from_cache": False, "error": ""}

    if not refresh and searched(person["name"], person["company"]):
        stored = details(person["name"], person["company"])
        return {
            "entries": cached(person["name"], person["company"]),
            "profile": stored["profile_url"],
            "emails": stored["emails"],
            "from_cache": True,
            "error": "",
        }

    try:
        entries, profile, documents = experience.gather(person)
    except SearchUnavailable as exc:
        log.warning("Manual lookup for %r could not search: %s", person["name"], exc)
        stored = details(person["name"], person["company"])
        return {
            "entries": cached(person["name"], person["company"]),
            "profile": stored["profile_url"],
            "emails": stored["emails"],
            "from_cache": bool(stored["profile_url"] or stored["emails"]),
            "error": str(exc),
        }

    # Only look for an address if one was not supplied.
    emails = [] if person["email"] else experience.emails_in(documents, person)

    if refresh:
        forget(person["name"], person["company"])
    save(person, entries, profile, emails)

    return {
        "entries": entries,
        "profile": profile,
        "emails": emails,
        "from_cache": False,
        "error": "",
    }
