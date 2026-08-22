"""Tests for email_parser: headers, dates, MIME bodies, quoted replies."""

import pytest
from conftest import attachment, message, multipart, part, simple_message

import email_parser


# --- recipient ---------------------------------------------------------------


@pytest.mark.parametrize(
    "to_header, name, address",
    [
        ("Yasharth Choudhary <yash@bhume.in>", "Yasharth Choudhary", "yash@bhume.in"),
        ("pruthvi.gr@inmobi.com", "", "pruthvi.gr@inmobi.com"),
        ('"Baghla, Devansh" <d@x.com>', "Baghla, Devansh", "d@x.com"),
        ("A <a@x.com>, B <b@y.com>", "A", "a@x.com"),
        ("=?UTF-8?B?TmFtw6k=?= <n@x.com>", "Namé", "n@x.com"),
        ("MIXED@Case.COM", "", "mixed@case.com"),
        ("", "", ""),
    ],
)
def test_recipient_parsing(to_header, name, address):
    parsed = email_parser.parse(simple_message("hi", to=to_header))
    assert parsed["recipient_name"] == name
    assert parsed["recipient_email"] == address


def test_quoted_name_with_comma_is_not_split():
    """A naive comma split would return 'Baghla' as the whole recipient."""
    parsed = email_parser.parse(simple_message("hi", to='"Baghla, Devansh" <d@x.com>'))
    assert parsed["recipient_email"] == "d@x.com"


# --- subject and date --------------------------------------------------------


def test_subject_is_rfc2047_decoded():
    parsed = email_parser.parse(simple_message("hi", subject="=?UTF-8?B?SGVsbG8g4pyT?="))
    assert parsed["subject"] == "Hello ✓"


@pytest.mark.parametrize(
    "date_header, expected",
    [
        ("Thu, 25 Jun 2026 16:04:07 +0530", "2026-06-25"),
        ("Tue, 23 Jun 2026 20:40:14 -0700", "2026-06-23"),
    ],
)
def test_date_normalised_across_offsets(date_header, expected):
    parsed = email_parser.parse(simple_message("hi", date=date_header))
    assert parsed["sent_date"] == expected


def test_date_falls_back_to_internal_date():
    """A junk Date header must not lose the message."""
    parsed = email_parser.parse(
        simple_message("hi", date="not a date", internal_date="1782000000000")
    )
    assert parsed["sent_date"] == "2026-06-21"


def test_thread_id_and_internal_date_are_exposed():
    parsed = email_parser.parse(
        simple_message("hi", msg_id="abc", thread_id="thr", internal_date="1782000000000")
    )
    assert parsed["message_id"] == "abc"
    assert parsed["thread_id"] == "thr"
    assert parsed["internal_date"] == 1782000000000


# --- body extraction ---------------------------------------------------------


def test_plain_body():
    assert email_parser.parse(simple_message("Hello there"))["body"] == "Hello there"


def test_plain_is_preferred_over_html():
    payload = multipart(
        "multipart/alternative",
        part("text/plain", "the plain one"),
        part("text/html", "<p>the html one</p>"),
    )
    assert email_parser.parse(message(payload))["body"] == "the plain one"


def test_html_only_is_converted_to_text():
    payload = multipart(
        "multipart/mixed",
        part("text/html", "<p>Dear X,</p><p>I&rsquo;m applying.</p>"),
        attachment(),
    )
    body = email_parser.parse(message(payload))["body"]
    assert "Dear X," in body
    assert "I’m applying." in body
    assert "<p>" not in body


def test_nested_multipart_is_walked():
    """text/plain two levels down, as Gmail nests alternative inside mixed."""
    payload = multipart(
        "multipart/mixed",
        multipart(
            "multipart/alternative",
            part("text/plain", "found me"),
            part("text/html", "<p>ignored</p>"),
        ),
        attachment(),
    )
    assert email_parser.parse(message(payload))["body"] == "found me"


def test_scripts_and_styles_are_stripped():
    payload = part("text/html", "<style>p{color:red}</style><p>keep</p><script>x()</script>")
    body = email_parser.parse(message(payload))["body"]
    assert body.strip() == "keep"


def test_whitespace_only_plain_falls_through_to_html():
    """A 2-byte text/plain part is not a body; the real text is in the HTML."""
    payload = multipart(
        "multipart/alternative",
        part("text/plain", "\r\n"),
        part("text/html", "<div>actual text</div>"),
    )
    assert email_parser.parse(message(payload))["body"] == "actual text"


def test_empty_body_is_empty_string():
    payload = multipart("multipart/mixed", part("text/html", "<div dir=auto></div>"), attachment())
    assert email_parser.parse(message(payload))["body"] == ""


def test_undecodable_part_does_not_raise():
    payload = {"mimeType": "text/plain", "body": {"data": "!!!not base64!!!", "size": 5}}
    assert email_parser.parse(message(payload))["body"] == ""


def test_non_utf8_bytes_do_not_raise():
    payload = {"mimeType": "text/plain", "body": {"data": "_w", "size": 1}}  # 0xff
    email_parser.parse(message(payload))  # replacement char, no exception


# --- quoted replies ----------------------------------------------------------


def test_quoted_reply_is_split_off():
    body = (
        "Hi Yash,\n\nSounds good.\n\nRegards\nDevansh\n\n"
        "On Thu, 25 Jun 2026 at 15:43, Yasharth <yash@bhume.in> wrote:\n"
        "> Can we chat?\n"
    )
    parsed = email_parser.parse(simple_message(body))
    assert "Sounds good." in parsed["body"]
    assert "Can we chat?" not in parsed["body"]
    assert "Can we chat?" in parsed["quoted_body"]


def test_bare_angle_quote_splits_without_a_marker():
    parsed = email_parser.parse(simple_message("My reply\n> their text\n> more"))
    assert parsed["body"] == "My reply"
    assert "their text" in parsed["quoted_body"]


def test_original_message_marker():
    parsed = email_parser.parse(
        simple_message("Mine\n\n----- Original Message -----\nTheirs")
    )
    assert parsed["body"] == "Mine"
    assert "Theirs" in parsed["quoted_body"]


def test_no_quote_leaves_quoted_body_empty():
    assert email_parser.parse(simple_message("Just mine"))["quoted_body"] == ""


# --- against the real saved payloads ----------------------------------------


def test_real_payloads_all_parse(real_payloads):
    """Every saved real message parses and yields a usable date."""
    for name, raw in real_payloads.items():
        parsed = email_parser.parse(raw)
        assert parsed["recipient_email"], f"{name}: no recipient"
        assert parsed["sent_date"], f"{name}: no date"
        assert parsed["thread_id"], f"{name}: no thread id"
