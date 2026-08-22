"""Previous work experience per person, cached in its own CSV.

`output/experience.csv` holds one row per company a person worked at, in order.

How a lookup works:

1. build a few search queries from the person's name, company and email domain
2. search DuckDuckGo's HTML endpoint (free, no key) for candidate pages
3. fetch those pages and pull the readable text out of them
4. only then ask the LLM to structure what was actually retrieved

The LLM is told to use nothing but the supplied text, so an unverifiable role is
omitted rather than guessed. Everything except the LLM call is free.
"""

import datetime
import json
import logging
import re
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

import company as company_rules
import config
import storage

log = logging.getLogger(__name__)


class SearchUnavailable(RuntimeError):
    """The search engine refused us - rate limited, blocked, or unreachable.

    Distinct from "searched and found nothing": a refusal must not be cached as a
    miss, or a temporary block would permanently mark someone as having no history.
    """

SEARCH_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = "Mozilla/5.0 (compatible; personal-email-tool/1.0)"
TIMEOUT = 10

MAX_RESULTS = 6  # search hits considered per person
MAX_PAGES = 4  # pages actually fetched
PAGE_CHARS = 4000  # text kept per page
COURTESY_DELAY = 0.7  # seconds between requests

# Sites that block automated fetches or return nothing useful. Their search
# snippets are still used - only the page fetch is skipped.
SKIP_FETCH = (
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "pinterest.com",
    "quora.com",
)


# --- the store ---------------------------------------------------------------


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


def profile_link(email: str) -> str:
    """The profile url stored for a person whose search found nothing."""
    email = (email or "").strip().lower()
    for row in storage.read_rows(config.EXPERIENCE_PATH):
        if (row.get("person_email") or "").strip().lower() != email:
            continue
        if not (row.get("company") or "").strip():
            return (row.get("source") or "").strip()
    return ""


def forget(email: str) -> int:
    """Drop everything stored for this person so they can be searched again."""
    email = (email or "").strip().lower()
    rows = storage.read_rows(config.EXPERIENCE_PATH)
    keep = [r for r in rows if (r.get("person_email") or "").strip().lower() != email]

    removed = len(rows) - len(keep)
    if removed:
        storage.write_csv(keep, config.EXPERIENCE_PATH, storage.EXPERIENCE_FIELDNAMES)
        log.info("Cleared %s stored rows for %s", removed, email)
    return removed


def save(person: dict, entries: list, profile: str = "") -> None:
    """Store a person's experience. An empty `entries` records the miss.

    `entries` is a list of {"company", "role", "dates", "source"} in order, most
    recent previous role first. On a miss, `profile` is kept on the blank row so
    the page can offer a link to check by hand.
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
        rows = [
            {**common, "position": 0, "company": "", "role": "", "dates": "", "source": profile}
        ]
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

    storage.append_csv(rows, config.EXPERIENCE_PATH, storage.EXPERIENCE_FIELDNAMES)


# --- gathering public information -------------------------------------------


def _queries(person: dict) -> list:
    """A few searches worth running for this person."""
    name = (person.get("name") or "").strip()
    current = (person.get("company") or "").strip()
    domain = (person.get("email") or "").split("@")[-1].strip().lower()

    if not name:
        return []

    queries = [f'"{name}" {current}'.strip()]
    queries.append(f'"{name}" (linkedin OR github OR resume OR cv)')
    if domain and domain not in company_rules.FREE_DOMAINS:
        queries.append(f'"{name}" {domain}')
    return queries


def _decode_result_url(href: str) -> str:
    """DuckDuckGo wraps results as /l/?uddg=<encoded>. Unwrap those."""
    if "uddg=" in href:
        target = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg")
        if target:
            return target[0]
    if href.startswith("//"):
        return "https:" + href
    return href


def _search(query: str) -> list:
    """[{'title', 'url', 'snippet'}] from DuckDuckGo's HTML endpoint."""
    try:
        response = requests.post(
            SEARCH_URL,
            data={"q": query},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SearchUnavailable(f"search request failed: {exc}") from exc

    # 202 plus an "anomaly" page is how DuckDuckGo rate limits automated use.
    if response.status_code == 202 or "anomaly" in response.text.lower():
        raise SearchUnavailable("search engine is rate limiting us")

    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    for block in soup.select("div.result")[:MAX_RESULTS]:
        link = block.select_one("a.result__a")
        if not link:
            continue
        snippet = block.select_one(".result__snippet")
        results.append(
            {
                "title": link.get_text(" ", strip=True),
                "url": _decode_result_url(link.get("href", "")),
                "snippet": snippet.get_text(" ", strip=True) if snippet else "",
            }
        )
    return results


def _fetch_text(url: str) -> str:
    """Readable text from a page, or "" if it cannot be fetched."""
    if any(blocked in url for blocked in SKIP_FETCH):
        return ""
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, allow_redirects=True
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        log.info("Could not fetch %s: %s", url, exc)
        return ""

    if "html" not in response.headers.get("content-type", "") and not response.text:
        return ""

    soup = BeautifulSoup(response.text[:300_000], "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "form", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return text[:PAGE_CHARS]


def _collect(person: dict) -> list:
    """[{'url', 'text'}] of public material about this person."""
    seen, documents = set(), []

    for query in _queries(person):
        for hit in _search(query):
            url = hit["url"]
            if not url or url in seen:
                continue
            seen.add(url)

            # The snippet is public information too, and it is all we get for
            # sites that refuse automated fetches.
            parts = [hit["title"], hit["snippet"]]
            if len(documents) < MAX_PAGES:
                time.sleep(COURTESY_DELAY)
                body = _fetch_text(url)
                if body:
                    parts.append(body)

            text = " ".join(p for p in parts if p).strip()
            if text:
                documents.append({"url": url, "text": text})

        # Enough to work with: skip the remaining queries rather than risk a block.
        if len(documents) >= MAX_PAGES:
            break
        time.sleep(COURTESY_DELAY)

    log.info("Collected %s documents for %s", len(documents), person.get("email"))
    return documents


def _profile_link(person: dict, documents: list) -> str:
    """A linkedin.com/in/ url from the results, preferring a corroborated one.

    Names are not unique, so a result whose text mentions the person's current
    company wins over the first hit.
    """
    candidates = [doc for doc in documents if "linkedin.com/in/" in doc["url"]]
    if not candidates:
        return ""

    current = (person.get("company") or "").strip().lower()
    if current:
        for doc in candidates:
            if current in doc["text"].lower():
                return doc["url"]
    return candidates[0]["url"]


# --- structuring what was collected -----------------------------------------

STRUCTURE_PROMPT = (
    "You extract a person's PREVIOUS work experience from supplied public web text.\n"
    "Rules:\n"
    "- Use only the supplied text. Never use outside knowledge and never guess.\n"
    "- Include a role only if the text clearly ties it to THIS person.\n"
    "- Names are not unique. Ignore a source unless something in it corroborates this\n"
    "  person - their current company, email domain, or field of work. A same-name\n"
    "  stranger's page must be skipped entirely.\n"
    "- Omit their current company; it is given to you for context only.\n"
    "- If a field is not stated, use null. If nothing is verifiable, return an empty list.\n"
    "- Order most recent previous role first.\n"
    'Reply with JSON only: {"experience": [{"company": str, "role": str|null, '
    '"dates": str|null, "source": str}]}\n'
    '"source" must be one of the SOURCE urls given to you.'
)


def _structure(person: dict, documents: list) -> list:
    """Ask the LLM to structure the collected text. [] if it cannot."""
    client = company_rules._client()  # same OpenAI client and credentials
    if client is None or not documents:
        return []

    import openai

    blocks = "\n\n".join(
        f"SOURCE {doc['url']}\n{doc['text']}" for doc in documents
    )
    prompt = (
        f"Person: {person.get('name')}\n"
        f"Current company (exclude from results): {person.get('company') or 'unknown'}\n"
        f"Email: {person.get('email')}\n\n"
        f"Public text retrieved about them:\n\n{blocks}"
    )

    try:
        response = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=[
                {"role": "system", "content": STRUCTURE_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=800,
            temperature=0,
        )
        answer = (response.choices[0].message.content or "").strip()
    except (openai.APIError, openai.OpenAIError, TypeError) as exc:
        log.warning("Experience structuring failed: %s", exc)
        return []

    try:
        entries = json.loads(answer).get("experience") or []
    except (ValueError, AttributeError):
        log.warning("Could not parse the model's JSON: %.200s", answer)
        return []

    return _clean(entries, documents, person)


def _clean(entries, documents, person: dict) -> list:
    """Keep well-formed entries, drop the current employer and bad sources."""
    urls = {doc["url"] for doc in documents}
    current = (person.get("company") or "").strip().lower()

    cleaned = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("company") or "").strip()
        if not name or name.lower() == current:
            continue
        source = (entry.get("source") or "").strip()
        cleaned.append(
            {
                "company": name,
                "role": (entry.get("role") or "").strip(),
                "dates": (entry.get("dates") or "").strip(),
                "source": source if source in urls else "",
            }
        )
    return cleaned


# --- entry points ------------------------------------------------------------


def find(person: dict) -> tuple[list, str]:
    """(entries, profile_url). Search public sources, then structure the findings.

    `profile_url` is only filled in when nothing verifiable was found, so the page
    can offer a profile to check by hand instead of showing an empty result.
    """
    if not config.ENABLE_EXPERIENCE_SEARCH:
        log.info("Experience search is switched off")
        return [], ""
    if not (person.get("name") or "").strip():
        return [], ""

    documents = _collect(person)
    if not documents:
        return [], ""

    entries = _structure(person, documents)
    if entries:
        return entries, ""
    return [], _profile_link(person, documents)


def lookup(person: dict, refresh: bool = False) -> tuple[list, bool, str]:
    """(entries, from_cache, error).

    `refresh` searches again even if this person was already done. The old rows
    are only replaced once a new search actually succeeds - a rate limit must not
    cost you results you already had.

    `error` is set when the search could not run at all. Nothing is written then,
    so the person stays retryable.
    """
    email = person.get("email") or ""
    if not refresh and searched(email):
        return cached(email), True, ""

    try:
        entries, profile = find(person)
    except SearchUnavailable as exc:
        log.warning("Search unavailable for %s: %s", email, exc)
        # Keep whatever is stored; report the failure rather than losing data.
        return cached(email), searched(email), str(exc)

    if refresh:
        forget(email)
    save(person, entries, profile=profile)
    return entries, False, ""
