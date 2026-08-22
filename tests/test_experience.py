"""Tests for the experience store: save, cache, ordering, and the miss marker."""

import pytest

import config
import experience
import storage


@pytest.fixture(autouse=True)
def temp_store(tmp_path, monkeypatch):
    """Point the experience CSV at a temp file for every test."""
    monkeypatch.setattr(config, "EXPERIENCE_PATH", tmp_path / "experience.csv")


PERSON = {"name": "Yathaarth Sharma", "company": "Magi", "email": "yathaarth@gmail.com"}


def test_unknown_person_has_nothing_and_was_not_searched():
    assert experience.cached(PERSON["email"]) == []
    assert experience.searched(PERSON["email"]) is False


def test_saved_entries_come_back_in_order():
    experience.save(
        PERSON,
        [
            {"company": "Acme", "role": "SDE", "source": "example.com/a"},
            {"company": "Initech", "role": "Intern", "source": "example.com/b"},
        ],
    )

    entries = experience.cached(PERSON["email"])
    assert [e["company"] for e in entries] == ["Acme", "Initech"]
    assert [e["position"] for e in entries] == ["1", "2"]
    assert entries[0]["role"] == "SDE"
    assert experience.searched(PERSON["email"]) is True


def test_person_details_are_stored_alongside():
    experience.save(PERSON, [{"company": "Acme", "role": "SDE", "source": ""}], profile="https://p")
    row = experience.cached(PERSON["email"])[0]
    assert row["person_name"] == "Yathaarth Sharma"
    assert row["current_company"] == "Magi"
    assert row["profile_url"] == "https://p"
    assert row["found_at"]


def test_a_miss_is_recorded_so_it_is_not_retried():
    experience.save(PERSON, [])
    assert experience.cached(PERSON["email"]) == []
    assert experience.searched(PERSON["email"]) is True


def test_role_may_be_missing():
    experience.save(PERSON, [{"company": "Acme"}])
    assert experience.cached(PERSON["email"])[0]["role"] == ""


def test_people_are_kept_separate():
    other = {"name": "Someone Else", "company": "Truva", "email": "other@truva.in"}
    experience.save(PERSON, [{"company": "Acme", "role": "SDE"}])
    experience.save(other, [{"company": "Foo", "role": "PM"}])

    assert [e["company"] for e in experience.cached(PERSON["email"])] == ["Acme"]
    assert [e["company"] for e in experience.cached(other["email"])] == ["Foo"]


def test_email_lookup_is_case_insensitive():
    experience.save(PERSON, [{"company": "Acme"}])
    assert experience.cached("YATHAARTH@GMAIL.COM")


def test_find_is_skipped_when_search_is_switched_off(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_EXPERIENCE_SEARCH", False)
    assert experience.find(PERSON) == ([], "")


def test_find_needs_a_name(monkeypatch):
    monkeypatch.setattr(experience, "_collect", lambda p: pytest.fail("should not search"))
    assert experience.find({"email": "x@y.com", "name": "", "company": "Z"}) == ([], "")


def test_a_fruitless_search_is_recorded_so_it_is_not_repeated(monkeypatch):
    """lookup stores the miss, so the next click does no network work at all."""
    monkeypatch.setattr(experience, "find", lambda person: ([], ""))

    entries, from_cache, error = experience.lookup(PERSON)
    assert (entries, from_cache, error) == ([], False, "")
    assert experience.searched(PERSON["email"]) is True

    monkeypatch.setattr(experience, "find", lambda person: pytest.fail("searched twice"))
    assert experience.lookup(PERSON) == ([], True, "")


def test_lookup_serves_the_cache_once_saved():
    experience.save(PERSON, [{"company": "Acme", "role": "SDE"}])
    entries, from_cache, error = experience.lookup(PERSON)
    assert (from_cache, error) == (True, "")
    assert [e["company"] for e in entries] == ["Acme"]


def test_lookup_uses_find_once_it_exists(monkeypatch):
    """Proves the seam: implementing find is enough to make this work end to end."""
    monkeypatch.setattr(
        experience,
        "find",
        lambda person: ([{"company": "Discovered", "role": "Engineer", "source": "x.com"}], ""),
    )

    entries, from_cache, error = experience.lookup(PERSON)
    assert (from_cache, error) == (False, "")
    assert entries[0]["company"] == "Discovered"
    # and it was persisted
    assert [e["company"] for e in experience.cached(PERSON["email"])] == ["Discovered"]


def test_header_is_written_once_when_appending():
    experience.save(PERSON, [{"company": "Acme"}])
    experience.save({"email": "b@x.com", "name": "B"}, [{"company": "Beta"}])
    text = config.EXPERIENCE_PATH.read_text(encoding="utf-8-sig")
    assert text.count("person_email") == 1
    assert len(storage.read_rows(config.EXPERIENCE_PATH)) == 2


# --- search and parsing helpers (no network) --------------------------------


def test_queries_use_name_company_and_domain():
    queries = experience._queries(PERSON)
    assert any("Magi" in q for q in queries)
    assert all('"Yathaarth Sharma"' in q for q in queries)
    # a free provider is not a useful search term
    assert not any("gmail.com" in q for q in queries)


def test_corporate_domain_becomes_a_query():
    queries = experience._queries({"name": "A B", "company": "Truva", "email": "a@truva.in"})
    assert any("truva.in" in q for q in queries)


def test_no_name_means_no_queries():
    assert experience._queries({"name": "", "company": "X", "email": "a@b.com"}) == []


def test_blocked_sites_are_not_fetched(monkeypatch):
    """Their snippets are still used, but no request is made to them."""
    monkeypatch.setattr(experience.requests, "get", lambda *a, **k: pytest.fail("fetched"))
    assert experience._fetch_text("https://www.linkedin.com/in/someone") == ""


# --- cleaning the model's output --------------------------------------------


def test_current_employer_is_dropped():
    docs = [{"url": "https://example.com/a", "text": "x"}]
    entries = experience._clean(
        [{"company": "Magi", "role": "SDE"}, {"company": "Acme", "role": "SDE"}],
        docs,
        PERSON,
    )
    assert [e["company"] for e in entries] == ["Acme"]


def test_invented_sources_are_discarded():
    docs = [{"url": "https://example.com/a", "text": "x"}]
    entry = experience._clean(
        [{"company": "Acme", "source": "https://not-retrieved.example/"}], docs, PERSON
    )[0]
    assert entry["source"] == ""


def test_nameless_and_malformed_entries_are_dropped():
    docs = [{"url": "u", "text": "x"}]
    assert experience._clean([{"company": ""}, "nonsense", {"role": "SDE"}], docs, PERSON) == []


def test_missing_fields_become_empty_not_invented():
    docs = [{"url": "u", "text": "x"}]
    entry = experience._clean([{"company": "Acme", "role": None, "dates": None}], docs, PERSON)[0]
    assert (entry["role"], entry["dates"]) == ("", "")


def test_structure_returns_nothing_without_documents():
    assert experience._structure(PERSON, []) == []


def test_full_flow_with_stubbed_search_and_llm(monkeypatch):
    """search -> fetch -> structure -> store, with no network and no API call."""
    monkeypatch.setattr(
        experience,
        "_collect",
        lambda person: [{"url": "https://example.com/bio", "text": "Ex-Acme engineer"}],
    )
    monkeypatch.setattr(
        experience,
        "_structure",
        lambda person, docs: [
            {"company": "Acme", "role": "Engineer", "dates": "2019-2021", "source": docs[0]["url"]}
        ],
    )

    entries, from_cache, error = experience.lookup(PERSON)
    assert (from_cache, error) == (False, "")
    assert entries[0]["company"] == "Acme"

    stored = experience.cached(PERSON["email"])[0]
    assert stored["role"] == "Engineer"
    assert stored["dates"] == "2019-2021"
    assert stored["source"] == "https://example.com/bio"


# --- the profile-link fallback ----------------------------------------------


def test_profile_link_is_returned_when_nothing_is_verifiable(monkeypatch):
    docs = [
        {"url": "https://example.com/blog", "text": "unrelated"},
        {"url": "https://in.linkedin.com/in/yathaarth", "text": "Yathaarth Sharma at Magi"},
    ]
    monkeypatch.setattr(experience, "_collect", lambda person: docs)
    monkeypatch.setattr(experience, "_structure", lambda person, d: [])

    entries, profile = experience.find(PERSON)
    assert entries == []
    assert profile == "https://in.linkedin.com/in/yathaarth"


def test_profile_link_is_returned_even_when_experience_was_found(monkeypatch):
    """The link is useful either way, so every search returns one."""
    docs = [{"url": "https://in.linkedin.com/in/yathaarth", "text": "at Magi"}]
    monkeypatch.setattr(experience, "_collect", lambda person: docs)
    monkeypatch.setattr(experience, "_structure", lambda person, d: [{"company": "Acme"}])

    entries, profile = experience.find(PERSON)
    assert [e["company"] for e in entries] == ["Acme"]
    assert profile == "https://in.linkedin.com/in/yathaarth"


def test_profile_is_stored_on_every_row_of_a_hit(monkeypatch):
    monkeypatch.setattr(experience, "_collect", lambda person: [
        {"url": "https://in.linkedin.com/in/yathaarth", "text": "at Magi"}
    ])
    monkeypatch.setattr(experience, "_structure", lambda person, d: [
        {"company": "Acme"}, {"company": "Initech"}
    ])

    experience.lookup(PERSON)
    rows = experience.cached(PERSON["email"])
    assert len(rows) == 2
    assert all(r["profile_url"] == "https://in.linkedin.com/in/yathaarth" for r in rows)
    assert experience.profile_link(PERSON["email"]) == "https://in.linkedin.com/in/yathaarth"


def test_corroborated_profile_wins_over_the_first_hit():
    """Same-name strangers rank below a profile mentioning the current company."""
    docs = [
        {"url": "https://in.linkedin.com/in/someone-else", "text": "a different person"},
        {"url": "https://in.linkedin.com/in/the-right-one", "text": "Co-Founder at Magi"},
    ]
    assert experience._profile_link(PERSON, docs) == "https://in.linkedin.com/in/the-right-one"


def test_first_profile_used_when_none_corroborate():
    docs = [{"url": "https://in.linkedin.com/in/a", "text": "nothing matching"}]
    assert experience._profile_link(PERSON, docs) == "https://in.linkedin.com/in/a"


def test_directory_pages_are_not_treated_as_profiles():
    docs = [{"url": "https://www.linkedin.com/pub/dir/Yathaarth/Sharma", "text": "4 profiles"}]
    assert experience._profile_link(PERSON, docs) == ""


def test_profile_link_survives_a_round_trip_through_the_csv(monkeypatch):
    monkeypatch.setattr(experience, "_collect", lambda person: [
        {"url": "https://in.linkedin.com/in/yathaarth", "text": "at Magi"}
    ])
    monkeypatch.setattr(experience, "_structure", lambda person, d: [])

    experience.lookup(PERSON)
    assert experience.profile_link(PERSON["email"]) == "https://in.linkedin.com/in/yathaarth"
    assert experience.cached(PERSON["email"]) == []  # still a miss, not an entry


def test_no_profile_link_for_an_unsearched_person():
    assert experience.profile_link("nobody@nowhere.com") == ""


# --- a refused search must not be cached as a miss --------------------------


def test_rate_limit_is_not_stored_as_no_experience(monkeypatch):
    """A temporary block must not permanently mark someone as having no history."""
    def blocked(person):
        raise experience.SearchUnavailable("search engine is rate limiting us")

    monkeypatch.setattr(experience, "find", blocked)

    entries, from_cache, error = experience.lookup(PERSON)
    assert entries == []
    assert from_cache is False
    assert "rate limiting" in error
    assert experience.searched(PERSON["email"]) is False, "nothing should be cached"


def test_the_person_can_be_retried_after_a_block(monkeypatch):
    def blocked(person):
        raise experience.SearchUnavailable("blocked")

    monkeypatch.setattr(experience, "find", blocked)
    experience.lookup(PERSON)

    monkeypatch.setattr(experience, "find", lambda person: ([{"company": "Acme"}], ""))
    entries, from_cache, error = experience.lookup(PERSON)
    assert [e["company"] for e in entries] == ["Acme"]
    assert (from_cache, error) == (False, "")


def test_collect_uses_the_provider_chain(monkeypatch):
    """experience delegates searching; it does not know about any one provider."""
    import search as search_module

    monkeypatch.setattr(search_module, "search", lambda q: [
        {"title": "T", "url": "https://example.com/bio", "snippet": "worked at Acme"}
    ])
    monkeypatch.setattr(experience, "_fetch_text", lambda url: "")

    docs = experience._collect(PERSON)
    assert [d["url"] for d in docs] == ["https://example.com/bio"]
    assert "worked at Acme" in docs[0]["text"]


def test_collect_deduplicates_across_queries(monkeypatch):
    """The same page found by two different queries is fetched once."""
    import search as search_module

    monkeypatch.setattr(search_module, "search", lambda q: [
        {"title": "T", "url": "https://example.com/bio", "snippet": "s"},
        {"title": "T", "url": "https://example.com/bio/", "snippet": "s"},
    ])
    monkeypatch.setattr(experience, "_fetch_text", lambda url: "")

    assert len(experience._collect(PERSON)) == 1


def test_a_provider_outage_propagates_as_unavailable(monkeypatch):
    import search as search_module

    def blocked(q):
        raise search_module.SearchUnavailable("all providers down")

    monkeypatch.setattr(search_module, "search", blocked)
    with pytest.raises(experience.SearchUnavailable):
        experience._collect(PERSON)


# --- searching again ---------------------------------------------------------


def test_forget_removes_only_that_person():
    other = {"name": "B", "company": "X", "email": "b@x.com"}
    experience.save(PERSON, [{"company": "Acme"}])
    experience.save(other, [{"company": "Beta"}])

    assert experience.forget(PERSON["email"]) == 1
    assert experience.searched(PERSON["email"]) is False
    assert [e["company"] for e in experience.cached(other["email"])] == ["Beta"]


def test_forget_on_an_unknown_person_is_a_no_op():
    assert experience.forget("nobody@nowhere.com") == 0


def test_refresh_discards_the_cache_and_searches_again(monkeypatch):
    experience.save(PERSON, [{"company": "Stale"}])

    monkeypatch.setattr(experience, "find", lambda person: ([{"company": "Fresh"}], ""))
    entries, from_cache, error = experience.lookup(PERSON, refresh=True)

    assert (from_cache, error) == (False, "")
    assert [e["company"] for e in entries] == ["Fresh"]
    assert [e["company"] for e in experience.cached(PERSON["email"])] == ["Fresh"]


def test_refresh_does_not_duplicate_rows(monkeypatch):
    monkeypatch.setattr(experience, "find", lambda person: ([{"company": "Acme"}], ""))
    experience.lookup(PERSON)
    experience.lookup(PERSON, refresh=True)
    assert len(experience.cached(PERSON["email"])) == 1


def test_refresh_clears_a_cached_miss(monkeypatch):
    """The point of the button: retry someone a rate limit or bad sources wrote off."""
    experience.save(PERSON, [])
    assert experience.searched(PERSON["email"]) is True

    monkeypatch.setattr(experience, "find", lambda person: ([{"company": "Found now"}], ""))
    entries, _, _ = experience.lookup(PERSON, refresh=True)
    assert [e["company"] for e in entries] == ["Found now"]


def test_a_failed_refresh_keeps_what_was_already_stored(monkeypatch):
    """A rate limit must not cost you results you already had."""
    experience.save(PERSON, [{"company": "Old", "role": "SDE"}])

    def blocked(person):
        raise experience.SearchUnavailable("rate limited")

    monkeypatch.setattr(experience, "find", blocked)
    entries, from_cache, error = experience.lookup(PERSON, refresh=True)

    assert "rate limited" in error
    assert [e["company"] for e in entries] == ["Old"], "old results must survive"
    assert [e["company"] for e in experience.cached(PERSON["email"])] == ["Old"]


def test_profile_link_reads_the_dedicated_column():
    experience.save(PERSON, [{"company": "Acme"}], profile="https://in.linkedin.com/in/x")
    assert experience.profile_link(PERSON["email"]) == "https://in.linkedin.com/in/x"


def test_profile_link_falls_back_to_older_rows(monkeypatch):
    """Rows written before profile_url existed kept it on the blank miss row."""
    storage.append_csv(
        [{
            "person_email": PERSON["email"], "person_name": "Y", "current_company": "Magi",
            "position": 0, "company": "", "role": "", "dates": "",
            "source": "https://in.linkedin.com/in/legacy", "found_at": "2026-01-01",
        }],
        config.EXPERIENCE_PATH,
        [f for f in storage.EXPERIENCE_FIELDNAMES if f != "profile_url"],
    )
    assert experience.profile_link(PERSON["email"]) == "https://in.linkedin.com/in/legacy"


# --- the always-available fallback ------------------------------------------


def test_manual_links_are_offered_for_every_person():
    labels = [label for label, _ in experience.manual_links(PERSON)]
    assert labels == ["LinkedIn", "Google", "DuckDuckGo"]


def test_manual_links_include_name_and_company():
    urls = dict((label, url) for label, url in experience.manual_links(PERSON))
    assert "Yathaarth+Sharma" in urls["LinkedIn"]
    assert "Magi" in urls["LinkedIn"]
    assert "%22Yathaarth+Sharma%22" in urls["Google"], "the name should be quoted for Google"


def test_manual_links_work_without_a_company():
    urls = dict(experience.manual_links({"name": "Solo Person", "company": "", "email": "a@b.c"}))
    assert "Solo+Person" in urls["LinkedIn"]


def test_manual_links_need_a_name():
    assert experience.manual_links({"name": "", "company": "Magi", "email": "a@b.c"}) == []


def test_manual_links_do_not_depend_on_search(monkeypatch):
    """They are built from the person alone, so a total outage still gives them."""
    def blocked(q):
        raise experience.SearchUnavailable("everything is down")

    import search as search_module
    monkeypatch.setattr(search_module, "search", blocked)
    assert len(experience.manual_links(PERSON)) == 3
