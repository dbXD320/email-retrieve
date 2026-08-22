"""Tests for the web layer's name handling."""

import app


def test_header_name_is_shown_as_is():
    name, derived = app.display_name(
        {"recipient_name": "Yathaarth Sharma", "recipient_email": "yathaarth@gmail.com"}
    )
    assert (name, derived) == ("Yathaarth Sharma", False)


def test_header_name_is_never_replaced_by_a_derived_one():
    """A name in the header must win even when the address would give a nicer one."""
    name, derived = app.display_name(
        {"recipient_name": "Bob", "recipient_email": "robert.anthony.smith@acme.com"}
    )
    assert (name, derived) == ("Bob", False)


def test_address_is_used_only_when_there_is_no_header_name():
    name, derived = app.display_name(
        {"recipient_name": "", "recipient_email": "pruthvi.gr@inmobi.com"}
    )
    assert (name, derived) == ("Pruthvi Gr", True)


def test_blank_row_does_not_crash():
    assert app.display_name({"recipient_name": "", "recipient_email": ""}) == ("-", True)


# --- navigation --------------------------------------------------------------


def make_client():
    return app.app.test_client()


def test_back_value_is_an_absolute_path():
    """A bare '?size=..' would resolve against /experience, not the list."""
    html = make_client().get("/?size=20&page=1").get_data(as_text=True)
    assert 'name="back" value="/?size=20&page=1"' in html


def test_experience_without_an_email_redirects_to_the_list():
    response = make_client().get("/experience?size=20&page=4")
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_experience_for_an_unknown_person_redirects_back():
    response = make_client().get("/experience?email=nobody@nowhere.test&back=/?size=10")
    assert response.status_code == 302
    assert response.headers["Location"] == "/?size=10"


def test_an_off_site_back_value_is_ignored():
    response = make_client().get("/experience?email=nobody@nowhere.test&back=https://evil.example/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/"
