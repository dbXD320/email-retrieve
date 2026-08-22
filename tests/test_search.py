"""Tests for the search-provider abstraction: fallback, cooldown, cache, dedupe."""

import time

import pytest

import config
import search


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Fresh cache file and cooldown state for every test."""
    monkeypatch.setattr(config, "SEARCH_CACHE_PATH", tmp_path / "search_cache.json")
    monkeypatch.setattr(search, "_cache", None)
    monkeypatch.setattr(search, "_cooldown", {})


def hit(url, title="t", snippet="s"):
    return {"title": title, "url": url, "snippet": snippet}


def use_providers(monkeypatch, order, **implementations):
    """Install fake providers and the order to try them in."""
    monkeypatch.setattr(config, "SEARCH_PROVIDERS", order)
    monkeypatch.setattr(search, "PROVIDERS", implementations)
    monkeypatch.setattr(search, "_configured", lambda name: name in implementations)


# --- fallback ----------------------------------------------------------------


def test_first_working_provider_wins(monkeypatch):
    calls = []

    def first(q):
        calls.append("first")
        return [hit("https://a.example/1")]

    def second(q):
        pytest.fail("second provider should not be reached")

    use_providers(monkeypatch, ["first", "second"], first=first, second=second)

    assert search.search("q") == [hit("https://a.example/1")]
    assert calls == ["first"]


def test_falls_through_to_the_next_provider_on_a_refusal(monkeypatch):
    def blocked(q):
        raise search.SearchUnavailable("rate limited")

    use_providers(
        monkeypatch, ["blocked", "working"],
        blocked=blocked, working=lambda q: [hit("https://b.example/1")],
    )

    results = search.search("q")
    assert [r["url"] for r in results] == ["https://b.example/1"]


def test_falls_through_the_whole_chain(monkeypatch):
    def blocked(q):
        raise search.SearchUnavailable("nope")

    use_providers(
        monkeypatch, ["one", "two", "three"],
        one=blocked, two=blocked, three=lambda q: [hit("https://c.example/1")],
    )
    assert len(search.search("q")) == 1


def test_all_providers_refusing_raises(monkeypatch):
    def blocked(q):
        raise search.SearchUnavailable("rate limited")

    use_providers(monkeypatch, ["one", "two"], one=blocked, two=blocked)

    with pytest.raises(search.SearchUnavailable) as caught:
        search.search("q")
    assert "one" in str(caught.value) and "two" in str(caught.value)


def test_an_empty_result_tries_the_next_provider(monkeypatch):
    """Nothing found is worth a second opinion, but it is not an error."""
    use_providers(
        monkeypatch, ["empty", "full"],
        empty=lambda q: [], full=lambda q: [hit("https://d.example/1")],
    )
    assert [r["url"] for r in search.search("q")] == ["https://d.example/1"]


def test_everyone_finding_nothing_is_an_answer_not_an_error(monkeypatch):
    use_providers(monkeypatch, ["a", "b"], a=lambda q: [], b=lambda q: [])
    assert search.search("q") == []


def test_unconfigured_providers_are_skipped(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_PROVIDERS", ["needs_key", "works"])
    monkeypatch.setattr(
        search, "PROVIDERS",
        {"needs_key": lambda q: pytest.fail("not configured"), "works": lambda q: [hit("https://e.example")]},
    )
    monkeypatch.setattr(search, "_configured", lambda name: name == "works")
    assert len(search.search("q")) == 1


def test_unknown_provider_name_is_reported_not_crashed(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_PROVIDERS", ["nonsense"])
    monkeypatch.setattr(search, "PROVIDERS", {})
    with pytest.raises(search.SearchUnavailable) as caught:
        search.search("q")
    assert "unknown provider" in str(caught.value)


# --- cooldown ----------------------------------------------------------------


def test_a_refused_provider_is_not_retried(monkeypatch):
    attempts = []

    def blocked(q):
        attempts.append(1)
        raise search.SearchUnavailable("rate limited")

    use_providers(
        monkeypatch, ["blocked", "working"],
        blocked=blocked, working=lambda q: [hit("https://f.example/" + q)],
    )

    search.search("first query")
    search.search("second query")
    assert len(attempts) == 1, "the blocked provider must be left alone"


def test_cooldown_expires(monkeypatch):
    attempts = []

    def blocked(q):
        attempts.append(1)
        raise search.SearchUnavailable("rate limited")

    use_providers(monkeypatch, ["blocked", "ok"], blocked=blocked, ok=lambda q: [hit("https://g.example/" + q)])
    monkeypatch.setattr(config, "SEARCH_COOLDOWN_SECONDS", 0)

    search.search("one")
    search.search("two")
    assert len(attempts) == 2, "a zero cooldown means it is tried again"


def test_status_reports_what_is_usable(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_PROVIDERS", ["duckduckgo", "brave"])
    monkeypatch.setattr(config, "BRAVE_API_KEY", "")
    states = dict(search.status())
    assert states["duckduckgo"] == "ready"
    assert states["brave"] == "not configured"


# --- cache -------------------------------------------------------------------


def test_the_same_query_is_not_searched_twice(monkeypatch):
    attempts = []

    def counting(q):
        attempts.append(q)
        return [hit("https://h.example/1")]

    use_providers(monkeypatch, ["counting"], counting=counting)

    search.search("same query")
    search.search("same query")
    assert attempts == ["same query"]


def test_the_cache_survives_a_restart(monkeypatch):
    use_providers(monkeypatch, ["p"], p=lambda q: [hit("https://i.example/1")])
    search.search("persisted")

    # a fresh process would reload from the file
    monkeypatch.setattr(search, "_cache", None)
    monkeypatch.setattr(search, "PROVIDERS", {"p": lambda q: pytest.fail("should be cached")})
    assert [r["url"] for r in search.search("persisted")] == ["https://i.example/1"]


def test_a_stale_cache_entry_is_re_searched(monkeypatch):
    use_providers(monkeypatch, ["p"], p=lambda q: [hit("https://j.example/old")])
    search.search("aging")

    monkeypatch.setattr(config, "SEARCH_CACHE_DAYS", -1)  # everything is stale
    monkeypatch.setattr(search, "_cache", None)
    monkeypatch.setattr(search, "PROVIDERS", {"p": lambda q: [hit("https://j.example/new")]})
    assert [r["url"] for r in search.search("aging")] == ["https://j.example/new"]


def test_an_empty_answer_is_cached_too(monkeypatch):
    attempts = []
    use_providers(monkeypatch, ["p"], p=lambda q: attempts.append(q) or [])
    search.search("nothing there")
    search.search("nothing there")
    assert len(attempts) == 1


# --- deduplication -----------------------------------------------------------


@pytest.mark.parametrize(
    "a, b",
    [
        ("https://x.example/page", "https://x.example/page/"),
        ("https://x.example/page", "https://X.Example/page"),
        ("https://x.example/page", "https://x.example/page#section"),
        ("https://x.example/page", "http://x.example/page"),
    ],
)
def test_equivalent_urls_are_deduplicated(a, b):
    assert len(search.dedupe([hit(a), hit(b)])) == 1


def test_different_urls_are_kept():
    assert len(search.dedupe([hit("https://x.example/a"), hit("https://x.example/b")])) == 2


def test_dedupe_keeps_the_first_occurrence():
    results = search.dedupe([hit("https://x.example/p", title="first"), hit("https://x.example/p/", title="second")])
    assert results[0]["title"] == "first"


def test_results_without_a_url_are_dropped():
    assert search.dedupe([hit(""), hit("https://x.example/a")]) == [hit("https://x.example/a")]


# --- provider details --------------------------------------------------------


def test_duckduckgo_redirects_are_unwrapped():
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fbio&rut=x"
    assert search._unwrap_ddg(wrapped) == "https://example.com/bio"
    assert search._unwrap_ddg("//example.com/p") == "https://example.com/p"
    assert search._unwrap_ddg("https://example.com/q") == "https://example.com/q"


def test_duckduckgo_treats_an_anomaly_page_as_unavailable(monkeypatch):
    class Response:
        status_code = 202
        text = "<html>anomaly detected</html>"

    monkeypatch.setattr(search.requests, "post", lambda *a, **k: Response())
    with pytest.raises(search.SearchUnavailable):
        search._duckduckgo("q")


def test_a_network_error_is_unavailable_not_a_crash(monkeypatch):
    def boom(*a, **k):
        raise search.requests.RequestException("no network")

    monkeypatch.setattr(search.requests, "post", boom)
    with pytest.raises(search.SearchUnavailable):
        search._duckduckgo("q")


@pytest.mark.parametrize("code", [202, 403, 429, 500, 503])
def test_busy_statuses_are_unavailable(monkeypatch, code):
    class Response:
        status_code = code
        text = "{}"

    monkeypatch.setattr(search.requests, "get", lambda *a, **k: Response())
    with pytest.raises(search.SearchUnavailable):
        search._get("https://example.com")


def test_google_cse_maps_its_fields(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return {"items": [{"title": "T", "link": "https://k.example/1", "snippet": "S"}]}

    monkeypatch.setattr(search, "_get", lambda *a, **k: Response())
    assert search._google_cse("q") == [{"title": "T", "url": "https://k.example/1", "snippet": "S"}]


def test_brave_maps_its_fields(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return {"web": {"results": [{"title": "T", "url": "https://l.example/1", "description": "D"}]}}

    monkeypatch.setattr(search.requests, "get", lambda *a, **k: Response())
    assert search._brave("q") == [{"title": "T", "url": "https://l.example/1", "snippet": "D"}]


def test_searx_maps_its_fields(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return {"results": [{"title": "T", "url": "https://m.example/1", "content": "C"}]}

    monkeypatch.setattr(config, "SEARX_URL", "https://searx.example/")
    monkeypatch.setattr(search, "_get", lambda *a, **k: Response())
    assert search._searx("q") == [{"title": "T", "url": "https://m.example/1", "snippet": "C"}]


def test_a_non_json_response_is_unavailable(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(config, "SEARX_URL", "https://searx.example")
    monkeypatch.setattr(search, "_get", lambda *a, **k: Response())
    with pytest.raises(search.SearchUnavailable):
        search._searx("q")


def test_configured_reflects_credentials(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CSE_KEY", "")
    monkeypatch.setattr(config, "GOOGLE_CSE_CX", "")
    assert search._configured("duckduckgo") is True
    assert search._configured("google_cse") is False

    monkeypatch.setattr(config, "GOOGLE_CSE_KEY", "k")
    monkeypatch.setattr(config, "GOOGLE_CSE_CX", "c")
    assert search._configured("google_cse") is True


# --- the lite endpoint -------------------------------------------------------


def test_lite_is_available_without_credentials():
    assert search._configured("duckduckgo_lite") is True


def test_lite_parses_results(monkeypatch):
    html = """
      <table>
        <tr><td><a class="result-link" href="https://a.example/1">First</a></td></tr>
        <tr><td class="result-snippet">about the first</td></tr>
        <tr><td><a class="result-link" href="https://b.example/2">Second</a></td></tr>
        <tr><td class="result-snippet">about the second</td></tr>
      </table>
    """

    class Response:
        status_code = 200
        text = html

    monkeypatch.setattr(search.requests, "post", lambda *a, **k: Response())
    results = search._duckduckgo_lite("q")
    assert [r["url"] for r in results] == ["https://a.example/1", "https://b.example/2"]
    assert results[0]["title"] == "First"
    assert results[0]["snippet"] == "about the first"


def test_lite_reports_a_rate_limit(monkeypatch):
    class Response:
        status_code = 200
        text = "<html>anomaly</html>"

    monkeypatch.setattr(search.requests, "post", lambda *a, **k: Response())
    with pytest.raises(search.SearchUnavailable):
        search._duckduckgo_lite("q")


def test_lite_covers_for_the_html_endpoint(monkeypatch):
    """The point of having both: one refusing must not end the search."""
    def blocked(q):
        raise search.SearchUnavailable("rate limited")

    monkeypatch.setattr(config, "SEARCH_PROVIDERS", ["duckduckgo", "duckduckgo_lite"])
    monkeypatch.setattr(search, "PROVIDERS", {
        "duckduckgo": blocked,
        "duckduckgo_lite": lambda q: [{"title": "t", "url": "https://ok.example/1", "snippet": "s"}],
    })
    monkeypatch.setattr(search, "_configured", lambda name: True)

    assert [r["url"] for r in search.search("q")] == ["https://ok.example/1"]


# --- throttling --------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_throttle(monkeypatch):
    """No real waiting in tests; the throttle tests set a delay themselves."""
    monkeypatch.setattr(search, "_last_request", 0.0)
    monkeypatch.setattr(config, "SEARCH_DELAY", 0)


def test_consecutive_requests_are_spaced(monkeypatch):
    """The second query must wait out SEARCH_DELAY."""
    slept, clock = [], [1000.0]
    monkeypatch.setattr(config, "SEARCH_DELAY", 6)
    monkeypatch.setattr(search.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(search.time, "sleep", lambda s: slept.append(s))
    use_providers(monkeypatch, ["p"], p=lambda q: [hit("https://a.example/" + q)])

    search.search("one")
    clock[0] += 2  # only 2s later
    search.search("two")

    assert slept == [4], "should wait the remaining 4 of the 6 seconds"


def test_no_wait_when_the_delay_has_already_passed(monkeypatch):
    slept, clock = [], [1000.0]
    monkeypatch.setattr(config, "SEARCH_DELAY", 6)
    monkeypatch.setattr(search.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(search.time, "sleep", lambda s: slept.append(s))
    use_providers(monkeypatch, ["p"], p=lambda q: [hit("https://a.example/" + q)])

    search.search("one")
    clock[0] += 30
    search.search("two")

    assert slept == []


def test_the_delay_applies_across_different_people(monkeypatch):
    """The limit is per client, so a different person's query waits too."""
    slept, clock = [], [1000.0]
    monkeypatch.setattr(config, "SEARCH_DELAY", 6)
    monkeypatch.setattr(search.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(search.time, "sleep", lambda s: slept.append(s))
    use_providers(monkeypatch, ["p"], p=lambda q: [hit("https://a.example/" + q)])

    search.search('"Person One" Acme')
    search.search('"Person Two" Initech')
    assert slept == [6]


def test_a_cached_query_does_not_wait(monkeypatch):
    slept = []
    monkeypatch.setattr(config, "SEARCH_DELAY", 6)
    monkeypatch.setattr(search.time, "sleep", lambda s: slept.append(s))
    use_providers(monkeypatch, ["p"], p=lambda q: [hit("https://a.example/1")])

    search.search("same")
    before = len(slept)
    search.search("same")  # served from cache
    assert len(slept) == before


def test_the_delay_also_precedes_a_fallback_provider(monkeypatch):
    """Falling through to another provider is another request, so it waits too."""
    slept, clock = [], [1000.0]
    monkeypatch.setattr(config, "SEARCH_DELAY", 6)
    monkeypatch.setattr(search.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(search.time, "sleep", lambda s: slept.append(s))

    def blocked(q):
        raise search.SearchUnavailable("rate limited")

    use_providers(monkeypatch, ["a", "b"], a=blocked, b=lambda q: [hit("https://ok.example/1")])
    search.search("q")
    assert slept == [6], "the second provider's request is throttled as well"


def test_searches_never_overlap(monkeypatch):
    """Two threads searching at once must be serialised by the lock."""
    import threading

    monkeypatch.setattr(config, "SEARCH_DELAY", 0)
    in_flight, overlaps = [], []

    def provider(query):
        in_flight.append(query)
        if len(in_flight) > 1:
            overlaps.append(tuple(in_flight))
        time.sleep(0.05)
        in_flight.remove(query)
        return [hit("https://a.example/" + query)]

    use_providers(monkeypatch, ["p"], p=provider)

    threads = [threading.Thread(target=search.search, args=(f"q{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert overlaps == [], f"requests overlapped: {overlaps}"
