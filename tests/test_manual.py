"""Tests for the manual lookup: its own store, email discovery, shared pipeline."""

import pytest

import config
import experience
import manual
import storage


@pytest.fixture(autouse=True)
def temp_stores(tmp_path, monkeypatch):
    """Both CSVs point at temp files, so nothing touches the real data."""
    monkeypatch.setattr(config, "MANUAL_PATH", tmp_path / "manual.csv")
    monkeypatch.setattr(config, "EXPERIENCE_PATH", tmp_path / "experience.csv")


@pytest.fixture
def found(monkeypatch):
    """Stub the pipeline: two roles, a profile, and a page mentioning two addresses."""
    docs = [
        {
            "url": "https://in.linkedin.com/in/asha",
            "text": "Asha Rao, Acme. Reach her at asha.rao@acme.com or asha.rao88@gmail.com. info@acme.com",
        }
    ]
    monkeypatch.setattr(
        experience,
        "gather",
        lambda person: (
            [
                {"company": "Initech", "role": "Engineer", "dates": "2019-2021", "source": docs[0]["url"]},
                {"company": "Globex", "role": "", "dates": "", "source": docs[0]["url"]},
            ],
            docs[0]["url"],
            docs,
        ),
    )
    return docs


# --- the separate store ------------------------------------------------------


def test_results_go_to_the_manual_csv_not_the_gmail_one(found):
    manual.lookup("Asha Rao", "Acme")

    assert storage.read_rows(config.MANUAL_PATH), "manual store should have rows"
    assert storage.read_rows(config.EXPERIENCE_PATH) == [], "gmail store must be untouched"


def test_rows_carry_the_person_and_order(found):
    manual.lookup("Asha Rao", "Acme")
    rows = manual.cached("Asha Rao", "Acme")

    assert [r["company"] for r in rows] == ["Initech", "Globex"]
    assert [r["position"] for r in rows] == ["1", "2"]
    assert rows[0]["person_name"] == "Asha Rao"
    assert rows[0]["current_company"] == "Acme"
    assert rows[0]["role"] == "Engineer"
    assert rows[0]["found_at"]


def test_identity_is_name_plus_company():
    assert manual.key_for("Asha Rao", "Acme") == "asha rao|acme"
    assert manual.key_for(" ASHA RAO ", " Acme ") == "asha rao|acme"


def test_the_same_name_at_different_companies_is_two_people(found):
    manual.lookup("Asha Rao", "Acme")
    manual.lookup("Asha Rao", "Initech")
    assert len({r["key"] for r in storage.read_rows(config.MANUAL_PATH)}) == 2


def test_previously_searched_people_are_listed(found):
    manual.lookup("Asha Rao", "Acme")
    manual.lookup("Bob Singh", "Globex")

    listed = [(p["name"], p["company"]) for p in manual.people()]
    assert ("Bob Singh", "Globex") in listed
    assert ("Asha Rao", "Acme") in listed
    assert len(listed) == 2, "one entry per person, not per row"


# --- caching, reusing the existing behaviour ---------------------------------


def test_a_second_lookup_is_served_from_the_store(found, monkeypatch):
    manual.lookup("Asha Rao", "Acme")
    monkeypatch.setattr(experience, "gather", lambda person: pytest.fail("searched twice"))

    result = manual.lookup("Asha Rao", "Acme")
    assert result["from_cache"] is True
    assert [e["company"] for e in result["entries"]] == ["Initech", "Globex"]


def test_refresh_searches_again_without_duplicating(found):
    manual.lookup("Asha Rao", "Acme")
    manual.lookup("Asha Rao", "Acme", refresh=True)
    assert [r["company"] for r in manual.cached("Asha Rao", "Acme")] == ["Initech", "Globex"]


def test_a_fruitless_search_is_recorded(monkeypatch):
    monkeypatch.setattr(experience, "gather", lambda person: ([], "", []))
    manual.lookup("Nobody Known", "Nowhere")

    assert manual.cached("Nobody Known", "Nowhere") == []
    assert manual.searched("Nobody Known", "Nowhere") is True


def test_a_blocked_search_keeps_what_was_stored(found, monkeypatch):
    manual.lookup("Asha Rao", "Acme")

    def blocked(person):
        raise manual.SearchUnavailable("rate limited")

    monkeypatch.setattr(experience, "gather", blocked)
    result = manual.lookup("Asha Rao", "Acme", refresh=True)

    assert "rate limited" in result["error"]
    assert [e["company"] for e in result["entries"]] == ["Initech", "Globex"]


def test_a_name_is_required(monkeypatch):
    monkeypatch.setattr(experience, "gather", lambda person: pytest.fail("should not search"))
    result = manual.lookup("", "Acme")
    assert result["entries"] == [] and result["error"] == ""


# --- finding an email --------------------------------------------------------


def test_emails_are_found_when_none_was_given(found):
    result = manual.lookup("Asha Rao", "Acme")
    by_kind = {e["kind"]: e["email"] for e in result["emails"]}

    assert by_kind["work"] == "asha.rao@acme.com"
    assert by_kind["personal"] == "asha.rao88@gmail.com"


def test_role_addresses_are_not_treated_as_the_person(found):
    result = manual.lookup("Asha Rao", "Acme")
    assert "info@acme.com" not in [e["email"] for e in result["emails"]]


def test_no_email_search_when_one_was_supplied(found):
    result = manual.lookup("Asha Rao", "Acme", email="asha@given.com")
    assert result["emails"] == []
    assert manual.details("Asha Rao", "Acme")["email_given"] == "asha@given.com"


def test_found_emails_are_stored_and_read_back(found):
    manual.lookup("Asha Rao", "Acme")
    stored = manual.details("Asha Rao", "Acme")
    assert "asha.rao@acme.com" in stored["emails"]
    assert stored["profile_url"] == "https://in.linkedin.com/in/asha"


# --- email extraction itself -------------------------------------------------


def test_an_unrelated_address_is_ignored():
    docs = [{"url": "u", "text": "Contact someone.else@random.org for details"}]
    assert experience.emails_in(docs, {"name": "Asha Rao", "company": "Acme"}) == []


def test_a_company_domain_address_counts_even_without_the_name():
    docs = [{"url": "u", "text": "Write to a.r@acme.com"}]
    found = experience.emails_in(docs, {"name": "Asha Rao", "company": "Acme"})
    assert [e["email"] for e in found] == ["a.r@acme.com"]


def test_free_provider_is_classified_personal():
    docs = [{"url": "u", "text": "asha.rao@gmail.com"}]
    assert experience.emails_in(docs, {"name": "Asha Rao", "company": "Acme"})[0]["kind"] == "personal"


def test_addresses_are_deduplicated():
    docs = [
        {"url": "a", "text": "asha.rao@acme.com"},
        {"url": "b", "text": "ASHA.RAO@acme.com"},
    ]
    assert len(experience.emails_in(docs, {"name": "Asha Rao", "company": "Acme"})) == 1


def test_nothing_is_invented_from_an_empty_page():
    assert experience.emails_in([{"url": "u", "text": ""}], {"name": "Asha Rao", "company": "Acme"}) == []
