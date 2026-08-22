"""Minimal web view of the extracted emails.

Reads the CSV that main.py wrote - no Gmail calls, so pages load instantly.
Run main.py first (or again) to refresh the data.

    venv\\Scripts\\python.exe app.py    ->  http://127.0.0.1:5000
"""

import math

from flask import Flask, render_template, request

import config
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
    rows = storage.read_rows(config.OUTPUT_PATH)

    size = request.args.get("size", type=int, default=DEFAULT_SIZE)
    if size not in PAGE_SIZES:
        size = DEFAULT_SIZE

    pages = max(1, math.ceil(len(rows) / size))
    page = min(max(request.args.get("page", type=int, default=1), 1), pages)

    start = (page - 1) * size
    visible = rows[start : start + size]

    return render_template(
        "index.html",
        rows=[(*display_name(r), r.get("company") or "") for r in visible],
        page=page,
        pages=pages,
        size=size,
        page_sizes=PAGE_SIZES,
        total=len(rows),
        first=start + 1 if visible else 0,
        last=start + len(visible),
        csv_path=config.OUTPUT_PATH,
    )


if __name__ == "__main__":
    app.run(debug=True)
