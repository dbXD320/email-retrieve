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
├── storage.py              # write results to CSV
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
Best-effort company guess from the parsed email, in priority order:
1. **Signature block** — last few lines of the body ("Regards, Asha — Acme Pvt Ltd").
2. **Body mentions** — regex for names followed by a legal suffix (Inc, Ltd, Pvt Ltd, LLP,
   GmbH), or phrases like "at <Company>" / "team at <Company>".
3. **Email domain fallback** — `asha@acme.com` -> `Acme`, skipped for free providers
   (gmail, yahoo, outlook, etc.).

Returns the company name (or empty string) plus which rule matched, so I can eyeball the
quality of the guesses in the CSV.

### `storage.py`
Writes the collected rows to `output/sent_emails.csv` with a fixed header:
`recipient_name, recipient_email, subject, sent_date, company, company_source`.
Opens the file once and streams rows.

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
(HTML body fallback), `pytest`.

## Flow
```
main.py
  -> gmail_client.fetch_sent_messages()   # raw Gmail dicts
  -> email_parser.parse(message)          # name, email, subject, date, body
  -> company.guess(parsed)                # company + source
  -> storage.write_rows(rows)             # output/sent_emails.csv
```
