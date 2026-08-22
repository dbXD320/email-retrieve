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
