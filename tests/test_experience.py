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
    experience.save(PERSON, [{"company": "Acme", "role": "SDE", "source": ""}])
    row = experience.cached(PERSON["email"])[0]
    assert row["person_name"] == "Yathaarth Sharma"
    assert row["current_company"] == "Magi"
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


def test_find_is_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        experience.find(PERSON)


def test_lookup_degrades_while_search_is_unbuilt():
    """No search yet, so nothing is returned and nothing is written."""
    entries, from_cache = experience.lookup(PERSON)
    assert (entries, from_cache) == ([], False)
    assert experience.searched(PERSON["email"]) is False


def test_lookup_serves_the_cache_once_saved():
    experience.save(PERSON, [{"company": "Acme", "role": "SDE"}])
    entries, from_cache = experience.lookup(PERSON)
    assert from_cache is True
    assert [e["company"] for e in entries] == ["Acme"]


def test_lookup_uses_find_once_it_exists(monkeypatch):
    """Proves the seam: implementing find is enough to make this work end to end."""
    monkeypatch.setattr(
        experience,
        "find",
        lambda person: [{"company": "Discovered", "role": "Engineer", "source": "x.com"}],
    )

    entries, from_cache = experience.lookup(PERSON)
    assert from_cache is False
    assert entries[0]["company"] == "Discovered"
    # and it was persisted
    assert [e["company"] for e in experience.cached(PERSON["email"])] == ["Discovered"]


def test_header_is_written_once_when_appending():
    experience.save(PERSON, [{"company": "Acme"}])
    experience.save({"email": "b@x.com", "name": "B"}, [{"company": "Beta"}])
    text = config.EXPERIENCE_PATH.read_text(encoding="utf-8-sig")
    assert text.count("person_email") == 1
    assert len(storage.read_rows(config.EXPERIENCE_PATH)) == 2
