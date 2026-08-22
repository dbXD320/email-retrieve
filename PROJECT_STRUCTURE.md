# Project Structure

Personal-use script that pulls every email I've sent via the Gmail API and extracts, per
recipient: name, email address, subject, sent date, and the recipient's company (inferred
mainly from the email body).

Deliberately flat: one package-less directory of small modules, no layers, no interfaces,
no dependency injection. Each file has one job and is imported directly by `main.py`.

```
email-retrieve/
├── main.py                 # entrypoint — runs the whole flow
├── config.py               # settings loaded from .env
├── gmail_client.py         # Gmail API auth + fetching sent messages
├── email_parser.py         # raw Gmail message -> recipient / subject / date / body
├── company.py              # guess the recipient's company from the body
├── storage.py              # write / read the CSVs (the caches)
├── search.py               # search providers with fallback, cooldown and a query cache
├── experience.py           # fetch + LLM structuring, cached to CSV
├── app.py                  # minimal Flask web view of the results
├── templates/
│   ├── index.html          # the list: table, page size, pagination
│   └── experience.html     # one person's previous roles
├── static/
│   └── style.css           # shared styles for both pages
├── requirements.txt        # dependencies
├── .env                    # settings: paths, message limit, date filter (gitignored)
├── .gitignore              # venv/, .env, credentials/, output/
├── README.md               # setup + how to run
├── credentials/            # Google OAuth client secret + cached token (gitignored)
├── output/                 # generated CSVs: sent_emails.csv, experience.csv (gitignored)
└── tests/
    ├── test_email_parser.py
    └── test_company.py
```

## Files

### `main.py`
The only orchestrator. Loads config, authenticates, loops over sent messages, calls the
parser, calls the company guesser, collects rows, writes the CSV, prints a short summary
(how many processed / how many companies found). Wraps the per-message work in a
`try/except` so one weird email can't kill the run.

### `config.py`
Reads `.env` (via `python-dotenv`) into plain module-level constants: credentials path,
token path, output CSV path, optional date filter, max messages, log level. One place to
change behaviour without touching code.

### `gmail_client.py`
Everything Google-specific:
- OAuth installed-app flow with the `gmail.readonly` scope, caching the token to
  `credentials/token.json` so login happens once.
- `fetch_sent_messages()` — queries `in:sent`, follows pagination, yields full raw message
  dicts one at a time so memory stays flat.
- Simple retry/sleep on rate-limit errors.

### `email_parser.py`
Turns one raw Gmail message dict into a small plain dict:
- Recipient name and email from the `To` header (handles `"Name" <a@b.com>` and bare
  addresses; takes the first recipient).
- `Subject` header.
- `Date` header normalised to a readable date.
- Body text: walks the MIME parts, base64url-decodes them, prefers `text/plain`, falls back
  to stripping tags from `text/html`.

This is the only file that knows Gmail's JSON shape.

### `company.py`
Best-effort company guess, three rules in priority order, stopping at the first hit:

1. **`content`** - the company named in the subject, body, or quoted reply, matched
   against the recipient's email domain so a short form (`Truva`) or a full name
   (`InMobi Technologies`) both work. Matching against the domain is what stops
   "BITS Pilani" - named in every one of these emails - from being returned as the
   recipient's employer. Hits inside an address or URL are ignored, and a name only ever
   seen lower-case gets capitalised.
2. **`pattern`** - the known sentence shape: `...opportunities at <Company>.` Anchored on
   a role keyword (opportunities, role, position, internship, opening, team), falling back
   to the last 2-3 meaningful words after the final `at` in a sentence. Sentences whose
   `at` follows a self-description (`student at`, `worked at`) are skipped - those describe
   the sender.
3. **`llm`** - ask OpenAI, sending **only** the recipient's address and the first 3
   non-empty lines of the body. Never the full body. Returns the company name or `null`.
   Answers are cached per corporate domain, so one company costs one call however many
   people you mailed there. Gated by `ENABLE_LLM_FALLBACK`; if no credentials are
   configured it logs once, disables itself, and the run continues on rules 1-2.

Returns `(company, source)`, and `source` lands in the CSV as `company_source` so I can
eyeball which rule produced each guess.

### `storage.py`
Writes the collected rows to `output/sent_emails.csv` with a fixed header:
`recipient_name, recipient_email, subject, sent_date, company, company_source`.
Opens the file once and streams rows.

### `app.py` and `templates/index.html`
A one-route Flask app that **processes on demand**, page by page:

- Opening a page asks for `page x size` records. Whatever is already in the CSV is reused;
  only the shortfall is fetched from Gmail, parsed, given a company, appended and saved.
  The mailbox is never processed upfront.
- The CSV is the cache, so revisiting a page is instant and no email is ever processed
  twice. `main.py` still exists for processing everything in one go.
- Thread rules are unchanged: a thread already resolved in the CSV keeps its company, and a
  new thread is extracted from its earliest email.
- Table of two columns only: recipient name and company. Page size 10 / 20 / 50 and
  first/prev/next pagination via plain `?size=&page=` links - no JavaScript, no build step.
- Reaching the end of the mailbox disables Next and says so; a page past the end clamps
  back. A CSV locked by Excel shows a note instead of a 500.
- Most sent mail has no display name in the `To` header, so a name derived from the address
  is shown in italics to distinguish it from a real one.

Start it with `python app.py` and open http://127.0.0.1:5000.

### `experience.py`
Previous work experience per person, cached in `output/experience.csv` - one row per
company, `position` 1 = most recent previous role:

    person_email, person_name, current_company, position, company, role, dates, source, found_at

A lookup runs in four steps, and the LLM only sees step 3's output:

1. `_queries` - a few searches from the name, current company and email domain (a free
   provider domain is not used as a search term).
2. `search.search` - the provider chain (see below). Results are deduplicated by
   normalised url, so the same page found twice is fetched once.
3. `_fetch_text` - fetches up to 4 pages with `requests`, strips script/style/nav with
   BeautifulSoup, keeps 4000 chars each, with a courtesy delay between requests. Sites that
   block automated fetches (LinkedIn, Instagram, ...) are not fetched at all - their search
   snippets are used instead, which is often where the useful text is.
4. `_structure` - the LLM turns the retrieved text into `{company, role, dates, source}`.
   It is told to use nothing but that text, to skip same-name strangers unless something
   corroborates the person, and to return null for anything not stated. `_clean` then drops
   the current employer, malformed entries, and any source url that was not actually
   retrieved - so an invented citation cannot survive.

When nothing verifiable is found, `_profile_link` returns a `linkedin.com/in/` url from the
search results so the page can offer something to check by hand. A profile whose text
mentions the person's current company wins over the first hit, since names are not unique;
`/pub/dir/` listing pages are not profiles and are ignored. The link is kept in the blank
row's `source` column, so it needs no extra schema.

`lookup` returns `(entries, from_cache, error)`. The cache is served when the person was
searched before, so a second click costs nothing. A fruitless search stores one blank row,
which is what stops it being repeated - but a search that could not run at all raises
`SearchUnavailable` and stores **nothing**, so a rate limit never gets mistaken for "this
person has no history". The free search endpoint does rate limit under repeated use; the
page says so and the person can be retried later. Switch the whole thing off with
`ENABLE_EXPERIENCE_SEARCH=false`.

Each row's **Find Experience** button posts to `/experience?email=...`, which renders the
companies, roles, dates and a link to each source.

### `search.py`
Search behind a provider abstraction, so no single engine can break the feature.

- **Order** comes from `SEARCH_PROVIDERS`. Each provider is tried in turn; a refusal
  (rate limit, 4xx/5xx, network error, unreadable response) moves on to the next. An empty
  result also falls through - worth a second opinion - but everyone finding nothing is
  recorded as a real answer, not an error.
- **Throttle**: one request at a time (a single lock across every provider), and at
  least `SEARCH_DELAY` seconds between requests - global, so searches for different
  people queue behind each other too. A cached query never waits.
- **Cooldown**: a provider that refuses is left alone for `SEARCH_COOLDOWN_SECONDS`
  (default 900) rather than retried on every lookup.
- **Query cache**: `output/search_cache.json`, keyed by query, valid for
  `SEARCH_CACHE_DAYS`. Shared across people, so overlapping queries cost nothing and the
  cache survives a restart.
- **Providers**: `duckduckgo` (HTML page, free, no key, rate limits readily),
  `duckduckgo_lite` (the lite endpoint, throttled independently in practice), `google_cse`
  (Google Custom Search JSON API, 100/day free, needs `GOOGLE_CSE_KEY` + `GOOGLE_CSE_CX`),
  `brave` (`BRAVE_API_KEY`), `searx` (`SEARX_URL`, an instance with JSON enabled). Each one
  enables itself once its credentials exist; `search.status()` reports what is usable.

Nothing here tries to disguise itself as a browser or evade a provider's limits - a refusal
is taken at face value and the next provider is used.

### `tests/`
Two pytest files covering the logic that's actually easy to get wrong, using hardcoded
sample payloads and strings — no network, no Google credentials needed:
- `test_email_parser.py` — header parsing, multipart and HTML-only bodies, odd characters.
- `test_company.py` — one case per extraction rule, plus the free-provider skip.

### `credentials/` and `output/`
Gitignored working directories. `credentials/` holds `client_secret.json` (downloaded from
Google Cloud Console) and the generated `token.json`; `output/` holds the CSV.

## Dependencies (`requirements.txt`)
`google-api-python-client`, `google-auth-oauthlib`, `python-dotenv`, `beautifulsoup4`
(HTML body fallback), `openai` (rule 3), `pytest`.

## Flow
```
main.py
  -> gmail_client.fetch_sent_messages()   # raw Gmail dicts
  -> email_parser.parse(message)          # name, email, subject, date, body
  -> company.guess(parsed)                # company + source
  -> storage.write_rows(rows)             # output/sent_emails.csv
```
