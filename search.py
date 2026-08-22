"""Web search behind a small provider abstraction.

Providers are tried in the order given by `SEARCH_PROVIDERS`. If one is rate
limited, unreachable, or errors, the next is tried; the failing one is put on a
cooldown so it is not hammered. Results are cached per query on disk, so the same
person is never searched twice.

Only `duckduckgo` works with no credentials. The others are free but need a key or
an instance url, and switch themselves on once configured:

    google_cse  GOOGLE_CSE_KEY + GOOGLE_CSE_CX   (100 queries/day free)
    brave       BRAVE_API_KEY                    (free tier)
    searx       SEARX_URL                        (a SearXNG instance with JSON on)

Nothing here tries to look like a browser or work around a provider's limits - a
refusal is taken at face value and the next provider is used instead.
"""

import datetime
import json
import logging
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

import config

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; personal-email-tool/1.0)"
TIMEOUT = 12
MAX_RESULTS = 6

# Statuses that mean "not now" rather than "no results".
BUSY_STATUSES = {202, 403, 429, 500, 502, 503, 504}


class SearchUnavailable(RuntimeError):
    """No provider could answer. Distinct from "searched and found nothing"."""


# --- per-provider cooldown ---------------------------------------------------

_cooldown = {}  # provider name -> monotonic time it becomes usable again


def _cooling(name: str) -> bool:
    until = _cooldown.get(name, 0)
    if until and time.monotonic() < until:
        return True
    _cooldown.pop(name, None)
    return False


def _rest(name: str, reason: str) -> None:
    _cooldown[name] = time.monotonic() + config.SEARCH_COOLDOWN_SECONDS
    log.warning(
        "%s unavailable (%s) - not retrying for %ss",
        name,
        reason,
        config.SEARCH_COOLDOWN_SECONDS,
    )


def status() -> list:
    """[(provider, state)] for reporting - configured, cooling, or unavailable."""
    report = []
    for name in config.SEARCH_PROVIDERS:
        if name not in PROVIDERS:
            report.append((name, "unknown provider"))
        elif not _configured(name):
            report.append((name, "not configured"))
        elif _cooling(name):
            left = int(_cooldown[name] - time.monotonic())
            report.append((name, f"cooling down, {left}s left"))
        else:
            report.append((name, "ready"))
    return report


# --- the query cache ---------------------------------------------------------

_cache = None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(config.SEARCH_CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _cache_get(query: str):
    entry = _load_cache().get(query)
    if not entry:
        return None
    try:
        age = datetime.date.today() - datetime.date.fromisoformat(entry["fetched"])
    except (KeyError, ValueError):
        return None
    if age.days > config.SEARCH_CACHE_DAYS:
        return None
    return entry.get("results", [])


def _cache_put(query: str, results: list) -> None:
    cache = _load_cache()
    cache[query] = {
        "fetched": datetime.date.today().isoformat(),
        "results": results,
    }
    try:
        config.SEARCH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SEARCH_CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not save the search cache: %s", exc)


# --- helpers -----------------------------------------------------------------


def _normalise(url: str) -> str:
    """A key for deduplication: no fragment, no trailing slash, lower-case host.

    Returns "" for anything without a host, so junk cannot become a valid key.
    """
    try:
        parts = urllib.parse.urlsplit((url or "").strip())
    except ValueError:
        return ""
    if not parts.netloc:
        return ""
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit(("https", parts.netloc.lower(), path, parts.query, ""))


def dedupe(results: list) -> list:
    """Drop repeats by normalised url, keeping the first of each."""
    seen, unique = set(), []
    for result in results:
        key = _normalise(result.get("url", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(result)
    return unique


def _get(url: str, **kwargs):
    """A GET that turns "busy" answers into SearchUnavailable."""
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, **kwargs
        )
    except requests.RequestException as exc:
        raise SearchUnavailable(str(exc)) from exc
    if response.status_code in BUSY_STATUSES:
        raise SearchUnavailable(f"HTTP {response.status_code}")
    return response


# --- providers ---------------------------------------------------------------


def _duckduckgo(query: str) -> list:
    """DuckDuckGo's no-JavaScript HTML page. Free, no key, rate limits readily."""
    try:
        response = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise SearchUnavailable(str(exc)) from exc

    # 202 with an "anomaly" page is how it says "you look automated".
    if response.status_code in BUSY_STATUSES or "anomaly" in response.text.lower():
        raise SearchUnavailable(f"rate limited (HTTP {response.status_code})")

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
                "url": _unwrap_ddg(link.get("href", "")),
                "snippet": snippet.get_text(" ", strip=True) if snippet else "",
            }
        )
    return results


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo wraps results as /l/?uddg=<encoded>."""
    if "uddg=" in href:
        target = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg")
        if target:
            return target[0]
    if href.startswith("//"):
        return "https:" + href
    return href


def _google_cse(query: str) -> list:
    """Google Custom Search JSON API. Free tier is 100 queries a day."""
    response = _get(
        "https://www.googleapis.com/customsearch/v1",
        params={
            "key": config.GOOGLE_CSE_KEY,
            "cx": config.GOOGLE_CSE_CX,
            "q": query,
            "num": MAX_RESULTS,
        },
    )
    try:
        items = response.json().get("items") or []
    except ValueError as exc:
        raise SearchUnavailable(f"unreadable response: {exc}") from exc

    return [
        {
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "snippet": item.get("snippet", ""),
        }
        for item in items[:MAX_RESULTS]
    ]


def _brave(query: str) -> list:
    """Brave Search API. Free tier, needs a key."""
    try:
        response = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": MAX_RESULTS},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "X-Subscription-Token": config.BRAVE_API_KEY,
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise SearchUnavailable(str(exc)) from exc
    if response.status_code in BUSY_STATUSES:
        raise SearchUnavailable(f"HTTP {response.status_code}")

    try:
        items = (response.json().get("web") or {}).get("results") or []
    except ValueError as exc:
        raise SearchUnavailable(f"unreadable response: {exc}") from exc

    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("description", ""),
        }
        for item in items[:MAX_RESULTS]
    ]


def _searx(query: str) -> list:
    """A SearXNG instance with the JSON API enabled."""
    response = _get(
        config.SEARX_URL.rstrip("/") + "/search",
        params={"q": query, "format": "json"},
    )
    try:
        items = response.json().get("results") or []
    except ValueError as exc:
        raise SearchUnavailable(f"instance did not return JSON: {exc}") from exc

    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
        }
        for item in items[:MAX_RESULTS]
    ]


PROVIDERS = {
    "duckduckgo": _duckduckgo,
    "google_cse": _google_cse,
    "brave": _brave,
    "searx": _searx,
}


def _configured(name: str) -> bool:
    """Whether this provider has what it needs to run."""
    if name == "duckduckgo":
        return True
    if name == "google_cse":
        return bool(config.GOOGLE_CSE_KEY and config.GOOGLE_CSE_CX)
    if name == "brave":
        return bool(config.BRAVE_API_KEY)
    if name == "searx":
        return bool(config.SEARX_URL)
    return False


# --- the entry point ---------------------------------------------------------


def search(query: str) -> list:
    """Results for `query`, from the cache or the first provider that answers.

    Falls through to the next provider on a refusal, and also when one simply
    returns nothing. Raises SearchUnavailable only if no provider could answer.
    """
    cached = _cache_get(query)
    if cached is not None:
        log.info("Search cache hit for %r", query)
        return cached

    problems, answered = [], False
    for name in config.SEARCH_PROVIDERS:
        provider = PROVIDERS.get(name)
        if provider is None:
            problems.append(f"{name}: unknown provider")
            continue
        if not _configured(name):
            continue
        if _cooling(name):
            problems.append(f"{name}: cooling down")
            continue

        try:
            results = dedupe(provider(query))
        except SearchUnavailable as exc:
            _rest(name, str(exc))
            problems.append(f"{name}: {exc}")
            continue

        answered = True
        if results:
            log.info("%s returned %s results for %r", name, len(results), query)
            _cache_put(query, results)
            return results
        log.info("%s returned nothing for %r, trying the next provider", name, query)

    if answered:
        # Everyone that could answer found nothing. That is a real answer.
        _cache_put(query, [])
        return []

    raise SearchUnavailable("; ".join(problems) or "no search providers configured")
