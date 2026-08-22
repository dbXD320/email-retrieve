"""Entrypoint: fetch sent mail, extract the fields, write the CSV.

The only orchestrator - every other module is a plain function it calls.
"""

import collections
import logging
import sys

import company
import config
import email_parser
import gmail_client
import storage

log = logging.getLogger(__name__)


def collect(service):
    """Parse every sent message. Returns (rows, failed_message_ids).

    A message that will not parse is logged and skipped - one odd email must not
    end a run over thousands.
    """
    rows, failed = [], []
    for message in gmail_client.fetch_sent_messages(service):
        message_id = message.get("id", "?")
        try:
            rows.append(email_parser.parse(message))
        except Exception as exc:
            failed.append(message_id)
            log.warning("Could not parse message %s: %s", message_id, exc)
    return rows, failed


def add_companies(rows):
    """Fill in company and company_source, extracting once per Gmail thread."""
    companies = company.guess_by_thread(rows)
    for row in rows:
        found, source = companies.get(row.get("thread_id", ""), ("", ""))
        row["company"] = found
        row["company_source"] = source
    return companies


def _thread_companies(rows) -> dict:
    """thread_id -> (company, source) for threads already resolved in the CSV."""
    known = {}
    for row in rows:
        company_name = (row.get("company") or "").strip()
        if company_name:
            known.setdefault(row.get("thread_id", ""), (company_name, row.get("company_source") or ""))
    return known


def ensure_processed(count: int):
    """Make sure at least `count` records are stored, processing more if needed.

    The CSV is the cache: rows already in it are never reprocessed. Anything
    missing is fetched from Gmail newest-first, parsed, given a company, appended
    and saved. Returns (rows, exhausted) - exhausted means Gmail had no more.
    """
    rows = storage.read_rows(config.OUTPUT_PATH)
    if len(rows) >= count:
        return rows, False

    service = gmail_client.get_service()
    known_ids = {r.get("message_id") for r in rows}
    wanted = count - len(rows)

    refs = []
    for ref in gmail_client.iter_sent_refs(service):
        if ref["id"] in known_ids:
            continue
        refs.append(ref)
        if len(refs) >= wanted:
            break
    exhausted = len(refs) < wanted

    parsed = []
    for ref in refs:
        try:
            parsed.append(email_parser.parse(gmail_client.fetch_message(service, ref["id"])))
        except Exception as exc:
            log.warning("Could not process %s: %s", ref["id"], exc)

    # A thread already resolved in the CSV keeps its company; new threads are
    # extracted from their earliest email, as always.
    from_csv = _thread_companies(rows)
    fresh = company.guess_by_thread([p for p in parsed if p["thread_id"] not in from_csv])

    for row in parsed:
        found, source = from_csv.get(row["thread_id"]) or fresh.get(row["thread_id"], ("", ""))
        row["company"] = found
        row["company_source"] = source

    rows += parsed
    storage.write_rows(rows, config.OUTPUT_PATH)
    log.info("Processed %s new emails, %s stored in total", len(parsed), len(rows))
    return rows, exhausted


def summarise(rows, failed, companies, out) -> None:
    found = sum(1 for r in rows if r["company"])
    by_source = collections.Counter(r["company_source"] for r in rows if r["company"])

    print()
    print(f"  emails written : {len(rows)}")
    print(f"  threads        : {len(companies)}")
    print(f"  with a company : {found}  ({len(rows) - found} blank)")
    for source, count in by_source.most_common():
        print(f"      via {source:<8} {count}")
    if failed:
        print(f"  failed to parse: {len(failed)} -> {', '.join(failed[:5])}")
    print(f"  output         : {out}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # The OpenAI SDK logs every request at INFO; we only want its warnings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    try:
        service = gmail_client.get_service()
    except FileNotFoundError as exc:
        print(f"Setup problem: {exc}", file=sys.stderr)
        return 1

    rows, failed = collect(service)
    if not rows:
        print("No sent messages matched the query.")
        return 0

    companies = add_companies(rows)
    out = storage.write_rows(rows, config.OUTPUT_PATH)
    summarise(rows, failed, companies, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
