"""Minimal web view of the extracted emails, processing on demand.

The CSV is the cache. Opening a page asks for `page * size` records; anything not
stored yet is fetched from Gmail, given a company, saved, and then shown. Nothing
is ever processed twice, and the mailbox is never processed upfront.

    venv\\Scripts\\python.exe app.py    ->  http://127.0.0.1:5000
"""

import math

from flask import Flask, redirect, render_template, request, url_for

import config
import experience as experience_store
import main
import storage

app = Flask(__name__)

PAGE_SIZES = (10, 20, 50)
DEFAULT_SIZE = 20


def display_name(row: dict) -> tuple[str, bool]:
    """(name, was_derived). Most sent mail has no display name in the To header,
    so fall back to the address's local part rather than showing a blank cell."""
    name = (row.get("recipient_name") or "").strip()
    if name:
        return name, False

    local = (row.get("recipient_email") or "").split("@")[0]
    pretty = " ".join(part.capitalize() for part in local.replace(".", " ").replace("_", " ").split())
    return pretty or "-", True


@app.route("/")
def index():
    size = request.args.get("size", type=int, default=DEFAULT_SIZE)
    if size not in PAGE_SIZES:
        size = DEFAULT_SIZE
    page = max(request.args.get("page", type=int, default=1), 1)

    stored_before = len(storage.read_rows(config.OUTPUT_PATH))

    # Process only as far as this page needs.
    try:
        rows, exhausted = main.ensure_processed(page * size)
        locked = False
    except PermissionError:
        # Typically the CSV is open in Excel, which locks it against writing.
        rows, exhausted, locked = storage.read_rows(config.OUTPUT_PATH), True, True
        app.logger.warning("Could not save %s - is it open elsewhere?", config.OUTPUT_PATH)

    # Past the end (Gmail ran out): fall back to the last page that has rows.
    pages = max(1, math.ceil(len(rows) / size))
    if page > pages:
        page = pages

    start = (page - 1) * size
    visible = rows[start : start + size]

    return render_template(
        "index.html",
        rows=[
            {
                "name": display_name(r)[0],
                "derived": display_name(r)[1],
                "company": r.get("company") or "",
                "email": r.get("recipient_email") or "",
            }
            for r in visible
        ],
        page=page,
        pages=pages,
        size=size,
        page_sizes=PAGE_SIZES,
        total=len(rows),
        first=start + 1 if visible else 0,
        last=start + len(visible),
        csv_path=config.OUTPUT_PATH,
        added=max(0, len(rows) - stored_before),
        exhausted=exhausted,
        locked=locked,
    )


@app.route("/experience")
def experience():
    """Show a person's previous roles, from the experience CSV.

    The search itself is not built yet (see experience.find), so for anyone not
    already in the CSV this shows what a lookup will be given.
    """
    email = (request.args.get("email") or "").strip().lower()

    # Only ever a path on this app, never an arbitrary url.
    back = request.args.get("back") or ""
    if not back.startswith("/"):
        back = url_for("index")

    row = next(
        (r for r in storage.read_rows(config.OUTPUT_PATH)
         if (r.get("recipient_email") or "").strip().lower() == email),
        None,
    )
    # Nothing to show without a known person - send them back to the list.
    if row is None:
        return redirect(back)

    person = {
        "name": display_name(row)[0],
        "company": row.get("company") or "",
        "email": email,
    }

    refresh = request.args.get("refresh") == "1"
    entries, from_cache, error = experience_store.lookup(person, refresh=refresh)
    app.logger.info(
        "Experience for %s: %s entries%s%s",
        email,
        len(entries),
        " (cached)" if from_cache else "",
        f" [{error}]" if error else "",
    )

    return render_template(
        "experience.html",
        person=person,
        back=back,
        entries=entries,
        from_cache=from_cache,
        searched=experience_store.searched(email),
        profile=experience_store.profile_link(email),
        error=error,
        refreshed=refresh,
        store_path=config.EXPERIENCE_PATH,
    )


if __name__ == "__main__":
    # No reloader: it would redo work on every file save.
    app.run(debug=True, use_reloader=False)
