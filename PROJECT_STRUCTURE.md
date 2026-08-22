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
├── storage.py              # write / read the CSV (the cache)
├── app.py                  # minimal Flask web view of the results
├── templates/
│   └── index.html          # the one page: table, page size, pagination
├── requirements.txt        # dependencies
├── .env                    # settings: paths, message limit, date filter (gitignored)
├── .gitignore              # venv/, .env, credentials/, output/
├── README.md               # setup + how to run
├── credentials/            # Google OAuth client secret + cached token (gitignored)
├── output/                 # generated CSV (gitignored)
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
