"""Tests for the three company-extraction rules and thread grouping."""

import pytest

import company

TEMPLATE = (
    "Dear Pruthvi,\n\nI hope you're doing well.\n\n"
    "I'm a final-year student at BITS Pilani, currently seeking Software "
    "Engineering and AI/ML opportunities at Magi.\n\nRegards\nDevansh"
)


def row(email="hr@example.com", subject="", body="", quoted="", **kwargs):
    """A parsed-email dict, the shape company.guess expects."""
    return {
        "recipient_email": email,
        "subject": subject,
        "body": body,
        "quoted_body": quoted,
        **kwargs,
    }


# --- rule 1: content ---------------------------------------------------------


def test_domain_name_found_in_body():
    got = company.guess(row(email="x@inmobi.com", body="I'd love to join InMobi."))
    assert got == ("InMobi", "content")


def test_full_name_with_suffix_is_kept():
    got = company.guess(row(email="x@inmobi.com", body="joining InMobi Technologies soon"))
    assert got == ("InMobi Technologies", "content")


def test_company_found_in_subject():
    got = company.guess(row(email="a@truva.in", subject="Truva - SDE application", body="Hi,\n\nSee attached."))
    assert got == ("Truva", "content")


def test_company_found_in_the_recipients_quoted_reply():
    got = company.guess(
        row(email="yash@bhume.in", body="Hi Yash,\n\nSounds good.", quoted="> Regards, Yasharth\n> Bhume Labs")
    )
    assert got == ("Bhume Labs", "content")


def test_address_in_the_text_is_not_a_mention():
    """'reply to a@acmecorp.com' must not count as naming Acmecorp."""
    got = company.guess(row(email="a@acmecorp.com", body="reply to me at a@acmecorp.com"))
    assert got == ("", "")


def test_lowercase_only_mention_is_capitalised():
    got = company.guess(row(email="a@acmecorp.com", body="see acmecorp for details"))
    assert got == ("Acmecorp", "content")


@pytest.mark.parametrize("email", ["f@gmail.com", "f@yahoo.co.in", "f@outlook.com", "f@proton.me"])
def test_free_providers_never_become_a_company(email):
    assert company.guess(row(email=email, body="here you go")) == ("", "")


# --- rule 2: the sentence pattern -------------------------------------------


def test_opportunities_at_is_extracted():
    """The documented pattern, on a domain that cannot corroborate it."""
    assert company.guess(row(email="hr@somewhere.io", body=TEMPLATE)) == ("Magi", "pattern")


def test_domain_match_wins_over_the_pattern():
    got = company.guess(row(email="p@magi.com", body=TEMPLATE))
    assert got == ("Magi", "content")


@pytest.mark.parametrize(
    "body, expected",
    [
        ("I am applying for openings at Acme.", "Acme"),
        ("Any internship at Zephyr Labs?", "Zephyr Labs"),
        ("I saw the role at Contoso listed.", "Contoso"),
        ("Regards to the team at Initech.", "Initech"),
    ],
)
def test_role_keywords(body, expected):
    assert company.guess(row(body=body)) == (expected, "pattern")


@pytest.mark.parametrize(
    "body",
    [
        "I'm a final-year student at BITS Pilani.",
        "I worked at a startup last year.",
        "I am based at home this week.",
    ],
)
def test_self_description_is_not_the_recipients_company(body):
    """'student at X' describes the sender, so it must not be returned."""
    assert company.guess(row(body=body)) == ("", "")


def test_role_keyword_still_wins_inside_a_self_describing_sentence():
    body = "I studied at BITS Pilani and am applying for openings at Acme."
    assert company.guess(row(body=body)) == ("Acme", "pattern")


def test_empty_body_finds_nothing():
    assert company.guess(row(email="a@gmail.com", body="")) == ("", "")


# --- rule 3: the LLM ---------------------------------------------------------


def test_third_line_is_what_gets_sent():
    assert company._third_line(TEMPLATE).startswith("I'm a final-year student")


def test_third_line_empty_when_body_is_short():
    assert company._third_line("one\n\ntwo") == ""


@pytest.fixture
def stub_llm(monkeypatch):
    """Capture the prompt instead of calling OpenAI. Returns the sent prompts."""
    import config

    sent = []

    def fake_ask(client, prompt):
        sent.append(prompt)
        return "Contoso" if "Contoso" in prompt else "null"

    monkeypatch.setattr(company, "_ask", fake_ask)
    monkeypatch.setattr(company, "_client", lambda: object())
    monkeypatch.setattr(config, "ENABLE_LLM_FALLBACK", True)
    monkeypatch.setattr(company, "_llm_cache", {})
    return sent


def test_llm_receives_only_the_address_and_third_line(stub_llm):
    body = "Hi,\n\nHope you are well.\n\nFollowing up on the Contoso partnership.\n\nRegards"
    got = company.guess(row(email="hr@unknown-co.xyz", body=body))

    assert got == ("Contoso", "llm")
    assert stub_llm == [
        "Email: hr@unknown-co.xyz\nLine: Following up on the Contoso partnership."
    ]


def test_llm_is_not_called_without_a_third_line(stub_llm):
    company.guess(row(email="hr@unknown-co.xyz", body="Hi,\n\nThanks"))
    assert stub_llm == []


def test_null_answer_leaves_the_company_blank(stub_llm):
    body = "Hi,\n\nHope you are well.\n\nJust checking in on the file.\n\nRegards"
    assert company.guess(row(email="hr@unknown-co.xyz", body=body)) == ("", "")


def test_answer_is_cached_per_domain(stub_llm):
    body = "Hi,\n\nHope you are well.\n\nFollowing up on the Contoso partnership.\n\nRegards"
    company.guess(row(email="one@unknown-co.xyz", body=body))
    company.guess(row(email="two@unknown-co.xyz", body=body))
    assert len(stub_llm) == 1, "second recipient at the same domain must reuse the answer"


def test_llm_is_skipped_when_disabled():
    """The autouse no_llm fixture disables it, so nothing should be attempted."""
    body = "Hi,\n\nHope you are well.\n\nFollowing up on the Contoso partnership.\n\nRegards"
    assert company.guess(row(email="hr@unknown-co.xyz", body=body)) == ("", "")


# --- thread grouping ---------------------------------------------------------


def thread_row(thread_id, internal_date, body="", **kwargs):
    return row(thread_id=thread_id, internal_date=internal_date, body=body, message_id=str(internal_date), **kwargs)


def test_company_comes_from_the_threads_first_email():
    """Gmail returns newest first; the earliest email must be the source."""
    rows = [
        thread_row("T1", 4000, "Hi,\n\nAny update?\n\nRegards"),
        thread_row("T1", 1000, TEMPLATE),
    ]
    assert company.guess_by_thread(rows) == {"T1": ("Magi", "pattern")}


def test_every_email_in_a_thread_shares_one_company():
    rows = [thread_row("T1", n, "Hi,\n\nAny update?\n\nRegards") for n in (4000, 3000, 2000)]
    rows.append(thread_row("T1", 1000, TEMPLATE))

    result = company.guess_by_thread(rows)
    assert len(result) == 1
    assert all(result[r["thread_id"]] == ("Magi", "pattern") for r in rows)


def test_threads_are_independent():
    rows = [
        thread_row("T1", 1000, TEMPLATE),
        thread_row("T2", 2000, "Hi,\n\nNothing here.\n\nRegards"),
    ]
    result = company.guess_by_thread(rows)
    assert result["T1"] == ("Magi", "pattern")
    assert result["T2"] == ("", "")


def test_thread_extraction_calls_the_llm_once(stub_llm):
    body = "Hi,\n\nHope you are well.\n\nFollowing up on the Contoso partnership.\n\nRegards"
    rows = [thread_row("T1", n, body, email="hr@unknown-co.xyz") for n in (1000, 2000, 3000, 4000)]

    assert company.guess_by_thread(rows) == {"T1": ("Contoso", "llm")}
    assert len(stub_llm) == 1, "one call for the whole thread"
