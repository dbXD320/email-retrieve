# email-retrieve

Personal script that pulls every email I've sent via the Gmail API and writes recipient
name, email, subject, sent date, and the recipient's company to a CSV.

See [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) for what each file does.

## Setup

1. Google Cloud Console: create a project, enable the **Gmail API**, add myself as a test
   user on the OAuth consent screen, create a **Desktop app** OAuth client.
2. Save the downloaded JSON as `credentials/client_secret.json`.
3. Adjust `.env` if needed (config.py defaults match it, so it is optional).
4. Install dependencies:

   ```
   venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

## Run

```
venv\Scripts\python.exe main.py
```

First run opens a browser for consent and caches the token to `credentials/token.json`;
later runs are silent. Output lands in `output/sent_emails.csv`.

## Browse the results

```
venv\Scripts\python.exe app.py
```

Then open http://127.0.0.1:5000 - a table of recipient and company, 10/20/50 per page.
It reads the CSV, so re-run `main.py` to refresh what it shows.

## Tests

```
venv\Scripts\python.exe -m pytest
```
