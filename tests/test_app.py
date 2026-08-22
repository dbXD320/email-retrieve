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
