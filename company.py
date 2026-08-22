"""Best-effort guess of the recipient's company.

Rules run in priority order and stop at the first hit:

1. content  - the company named in the subject or body, matched against the
              recipient's email domain so a short form or a full name both work
2. pattern  - "... opportunities at <Company>." and similar sentences, falling
              back to the last few meaningful words after "at"
3. llm      - ask OpenAI, sending only the address and the first 3 body lines
"""

import logging
import re

import config

log = logging.getLogger(__name__)

# Personal addresses tell us nothing about a company.
FREE_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "outlook.com",
    "hotmail.com", "live.com", "icloud.com", "me.com", "aol.com",
    "protonmail.com", "proton.me", "zoho.com", "rediffmail.com",
}

# Words that may trail a company name and belong to it.
SUFFIXES = (
    r"(?:Inc|LLC|Ltd|Limited|Pvt|Private|LLP|PLC|Corp|Corporation|Company|"
    r"Technologies|Technology|Labs|Systems|Solutions|Software|Group|Holdings|"
    r"Ventures|Studios|Media|GmbH|AG)"
)

# Sentences that name where an opportunity is: "...opportunities at Magi."
ROLE_AT = re.compile(
    r"\b(?:opportunit(?:y|ies)|role|roles|position|positions|internship|"
    r"internships|opening|openings|job|jobs|vacancy|vacancies|team)\b"
    r"[^.!?\n]{0,60}?\bat\s+(?P<company>[^.!?\n]{2,60})",
    re.IGNORECASE,
)

# "student at X", "interning at X" describe the sender, not the recipient.
SELF_CONTEXT = {
    "student", "studying", "study", "intern", "interned", "interning",
    "work", "working", "worked", "employed", "based", "graduated",
    "graduating", "enrolled", "am", "was",
}

# Words that are never a company name on their own.
STOPWORDS = {
    "the", "a", "an", "your", "you", "their", "our", "my", "his", "her", "its",
    "this", "that", "these", "those", "any", "some", "such", "and", "or",
    "present", "least", "most", "all", "both",
}


# --- rule 1: the company named in the content -------------------------------


def _domain(email: str) -> str:
    return email.split("@")[-1].strip().lower() if "@" in email else ""


def _domain_token(email: str) -> str:
    """'inmobi' from 'x@inmobi.com'. Empty for free providers."""
    domain = _domain(email)
    if not domain or domain in FREE_DOMAINS:
        return ""
    label = domain.split(".")[0]
    return label if len(label) > 2 else ""


def _from_content(parsed: dict):
    """Find the domain's company name in the subject or body, as written there.

    Matching against the domain is what stops "BITS Pilani" - mentioned in every
    one of these emails - from being returned as the recipient's employer.
    """
    token = _domain_token(parsed.get("recipient_email", ""))
    if not token:
        return None

    haystack = "\n".join(
        [
            parsed.get("subject", ""),
            parsed.get("body", ""),
            parsed.get("quoted_body", ""),
        ]
    )
    # The token as a whole word, plus any suffix written right after it.
    pattern = re.compile(
        rf"\b({re.escape(token)}(?:\s+{SUFFIXES})?)\b", re.IGNORECASE
    )
    lowercase_match = None
    for match in pattern.finditer(haystack):
        # Skip hits inside an address or URL: "yash@bhume.in" is not a mention.
        preceding = haystack[match.start() - 1] if match.start() else " "
        if preceding in "@/.-":
            continue
        text = match.group(1)
        if text[0].isupper():
            return _tidy_name(text)  # written as a name, keep that spelling
        lowercase_match = lowercase_match or text

    # Only ever seen lower-case, e.g. in a signature URL - capitalise it.
    return _tidy_name(lowercase_match.title()) if lowercase_match else None


# --- rule 2: the known sentence pattern -------------------------------------


def _tidy_name(name: str) -> str:
    """Trim punctuation and trailing filler from a captured name."""
    name = re.sub(r"\s+", " ", name).strip(" \t,;:-.'\"()")
    words = [w for w in name.split() if w]
    while words and words[-1].lower() in STOPWORDS:
        words.pop()
    while words and words[0].lower() in STOPWORDS:
        words.pop(0)
    return " ".join(words[:4])


def _looks_like_name(name: str) -> bool:
    """A company name has a capital letter and is not a whole sentence."""
    return bool(name) and len(name.split()) <= 4 and any(c.isupper() for c in name)


def _tail_after_at(sentence: str):
    """The last 2-3 meaningful words after the final 'at' in a sentence."""
    parts = re.split(r"\bat\b", sentence, flags=re.IGNORECASE)
    if len(parts) < 2:
        return None

    # "...student at BITS Pilani" is about me, not the recipient.
    before = parts[-2].split()
    if before and before[-1].lower().strip(",;:") in SELF_CONTEXT:
        return None

    candidate = _tidy_name(" ".join(parts[-1].split()[:3]))
    return candidate if _looks_like_name(candidate) else None


def _from_pattern(parsed: dict):
    """The company after 'opportunities at', or the tail of that sentence."""
    body = parsed.get("body", "")

    match = ROLE_AT.search(body)
    if match:
        # Keep only the leading name-like words: "Magi" from "Magi I am open to".
        candidate = _tidy_name(" ".join(_tidy_name(match.group("company")).split()[:3]))
        if _looks_like_name(candidate):
            return candidate

    # No keyword matched: try the last "at" of each sentence that has one.
    for sentence in re.split(r"(?<=[.!?])\s+|\n", body):
        if re.search(r"\bat\b", sentence, re.IGNORECASE):
            candidate = _tail_after_at(sentence)
            if candidate:
                return candidate
    return None


# --- rule 3: ask the LLM ----------------------------------------------------

SYSTEM_PROMPT = (
    "You identify which company a person belongs to. "
    "Reply with the company name only, nothing else. "
    "Reply with exactly null if you cannot determine it."
)

_llm_client = None
_llm_cache = {}


def _client():
    """The OpenAI client, or None if no credentials are configured.

    Construction itself fails when there is no key, so turn the fallback off
    rather than retrying it once per message.
    """
    global _llm_client
    if _llm_client is None:
        from openai import OpenAI

        try:
            _llm_client = OpenAI()  # reads OPENAI_API_KEY
        except Exception as exc:
            log.warning("No OpenAI credentials (%s), disabling LLM lookups", exc)
            config.ENABLE_LLM_FALLBACK = False
            return None
    return _llm_client


def _first_lines(body: str, count: int = 3) -> str:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return "\n".join(lines[:count])


def _ask(client, prompt: str) -> str:
    """One chat completion. Retries once for models that renamed the token cap."""
    import openai

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    try:
        response = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            max_tokens=32,
            temperature=0,
        )
    except openai.BadRequestError as exc:
        # Newer models reject max_tokens and a non-default temperature.
        if "max_tokens" not in str(exc) and "temperature" not in str(exc):
            raise
        log.info("Retrying %s without max_tokens/temperature", config.LLM_MODEL)
        response = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            max_completion_tokens=32,
        )
    return (response.choices[0].message.content or "").strip()


def _from_llm(parsed: dict):
    """Ask the LLM, sending only the address and the first 3 lines of the body."""
    if not config.ENABLE_LLM_FALLBACK:
        return None

    import openai

    email = parsed.get("recipient_email", "")
    if not email:
        return None

    domain = _domain(email)
    # One company per corporate domain, so ask once and reuse the answer.
    cacheable = bool(domain) and domain not in FREE_DOMAINS
    if cacheable and domain in _llm_cache:
        return _llm_cache[domain]

    prompt = (
        f"Email address: {email}\n"
        f"First lines of a message sent to them:\n"
        f"{_first_lines(parsed.get('body', ''))}"
    )

    client = _client()
    if client is None:
        return None

    try:
        answer = _ask(client, prompt)
    except openai.AuthenticationError:
        log.warning("OPENAI_API_KEY missing or invalid, disabling LLM lookups")
        config.ENABLE_LLM_FALLBACK = False
        return None
    except openai.NotFoundError:
        log.warning("Model %r not available, disabling LLM lookups", config.LLM_MODEL)
        config.ENABLE_LLM_FALLBACK = False
        return None
    except openai.RateLimitError:
        log.warning("LLM rate limited, skipping %s", email)
        return None
    except openai.APIConnectionError:
        log.warning("LLM unreachable, skipping %s", email)
        return None
    except openai.APIStatusError as exc:
        log.warning("LLM error %s, skipping %s", exc.status_code, email)
        return None

    company = None if answer.lower() in {"null", "none", "unknown", ""} else _tidy_name(answer)

    log.info("LLM: %s -> %s", email, company)
    if cacheable:
        _llm_cache[domain] = company
    return company


# --- entry point ------------------------------------------------------------

RULES = (
    ("content", _from_content),
    ("pattern", _from_pattern),
    ("llm", _from_llm),
)


def guess(parsed: dict) -> tuple[str, str]:
    """Return (company, source). Both empty when no rule finds anything."""
    for source, rule in RULES:
        try:
            company = rule(parsed)
        except Exception as exc:  # one odd email must not stop the run
            log.warning("Rule %s failed for %s: %s", source, parsed.get("recipient_email"), exc)
            continue
        if company:
            return company, source
    return "", ""


if __name__ == "__main__":
    # Extraction check: writes the guessed company per message to a CSV.
    import email_parser
    import gmail_client
    import storage

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    rows = []
    for message in gmail_client.fetch_sent_messages(gmail_client.get_service()):
        parsed = email_parser.parse(message)
        found, source = guess(parsed)
        rows.append(
            {
                **parsed,
                "company": found,
                "company_source": source,
                "body_preview": " ".join(parsed["body"].split())[:120],
            }
        )

    out = storage.write_csv(
        rows,
        config.OUTPUT_PATH.with_name("step5_companies.csv"),
        [
            "recipient_name",
            "recipient_email",
            "subject",
            "sent_date",
            "company",
            "company_source",
            "body_preview",
        ],
    )
    hits = sum(1 for r in rows if r["company"])
    print(f"\n{hits}/{len(rows)} messages got a company -> {out}")
