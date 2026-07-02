from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta

from .dedupe import dedupe_jobs
from .digest import render_digest
from .email_filter import is_likely_job_email, is_obvious_non_job_row
from .email_sources import EmailSource, GmailImapEmailSource, LocalFixtureEmailSource
from .parser import parse_email
from .scorer import score_job
from .storage import (
    delete_job_by_id,
    fetch_all_jobs,
    fetch_job_by_id,
    initialize_storage,
    update_job_status,
    upsert_scored_jobs,
)


def run_pipeline(email_source: EmailSource, source_label: str) -> int:
    """Run the pipeline with any email source implementation.

    Local fixtures and future Gmail ingestion should both enter Beacon here as
    `SourceEmail` objects. Everything after loading stays source-agnostic.
    """

    print("Beacon pipeline")
    emails = email_source.load_emails()
    print(f"Loaded {len(emails)} emails from {source_label}.")
    candidate_emails = [email for email in emails if is_likely_job_email(email)]
    skipped_email_count = len(emails) - len(candidate_emails)
    print(f"Filtered out {skipped_email_count} non-job emails before parsing.")

    if not candidate_emails:
        print("Add .txt fixtures to samples/emails to start the local pipeline.")
        return 0

    # `parse_email` returns a list because real job-alert emails can contain
    # multiple roles. The current fixtures happen to have one job each.
    jobs = [job for email in candidate_emails for job in parse_email(email)]
    print(f"Parsed {len(jobs)} job opportunities.")

    deduped_jobs, duplicate_count = dedupe_jobs(jobs)
    print(f"Removed {duplicate_count} duplicate job opportunities.")

    scored_jobs = [score_job(job) for job in deduped_jobs]
    connection = initialize_storage()
    stored_count = upsert_scored_jobs(scored_jobs, connection)
    connection.close()
    print(f"Stored {stored_count} scored job opportunities in SQLite.")

    print()
    print(render_digest(scored_jobs))
    return 0


def run_local() -> int:
    """Run the local fixture-based pipeline."""

    return run_pipeline(
        email_source=LocalFixtureEmailSource(),
        source_label="local fixtures",
    )


def run_gmail() -> int:
    """Run the pipeline against Gmail through IMAP and local `.env` secrets."""

    return run_pipeline(
        email_source=GmailImapEmailSource(),
        source_label="Gmail IMAP",
    )


def list_gmail_mailboxes() -> int:
    """Print Gmail IMAP mailbox names for label configuration."""

    source = GmailImapEmailSource()
    mailboxes = source.list_mailboxes()
    if not mailboxes:
        print("No Gmail IMAP mailboxes found.")
        return 0

    print("Gmail IMAP mailboxes:")
    for mailbox in mailboxes:
        print(f"- {mailbox}")
    return 0


def list_jobs() -> int:
    """Print a compact table of stored jobs."""

    connection = initialize_storage()
    rows = fetch_all_jobs(connection)
    connection.close()

    if not rows:
        print("No stored jobs found. Run `python -m beacon.main run-local` first.")
        return 0

    print("ID  Score  Category     Status            Seen  Posted       Added             Company        Title")
    print("--  -----  -----------  ----------------  ----  -----------  ----------------  -------------  -----")
    for row in rows:
        print(
            f"{row['id']:<2}  "
            f"{row['score']:<5}  "
            f"{row['category']:<11}  "
            f"{row['status']:<16}  "
            f"{row['seen_count']:<4}  "
            f"{_truncate(row['posted_date'] or 'Unknown', 11):<11}  "
            f"{_format_table_timestamp(row['created_at']):<16}  "
            f"{_truncate(_console_text(row['company']), 13):<13}  "
            f"{_console_text(row['title'])}"
        )
    return 0


def digest_jobs(
    since_hours: int = 24,
    limit: int = 10,
    include_investigate: bool = False,
    now: datetime | None = None,
) -> int:
    """Print a focused digest of recent high-priority stored jobs."""

    connection = initialize_storage()
    rows = fetch_all_jobs(connection)
    connection.close()

    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(hours=since_hours)
    visible_categories = ("Apply now", "Investigate") if include_investigate else ("Apply now",)
    digest_rows = [
        row
        for row in rows
        if row["category"] in visible_categories
        and _parse_stored_timestamp(row["created_at"], fallback=current_time) >= cutoff
    ][:limit]

    print(_render_stored_digest(digest_rows, since_hours=since_hours, include_investigate=include_investigate))
    return 0


def show_job(job_id: int) -> int:
    """Print detailed information for one stored job."""

    connection = initialize_storage()
    row = fetch_job_by_id(connection, job_id)
    connection.close()

    if row is None:
        print(f"No job found with id {job_id}.")
        return 1

    print(f"{row['company']} - {row['title']}")
    print(f"ID: {row['id']}")
    print(f"Score: {row['score']}")
    print(f"Category: {row['category']}")
    print(f"Status: {row['status']}")
    print(f"Location: {row['location'] or 'Unknown location'}")
    print(f"Work mode: {row['work_mode'] or 'Unknown'}")
    print(f"Salary: {row['salary_range'] or 'Unknown'}")
    print(f"Posted: {row['posted_date'] or 'Unknown'}")
    print(f"Link: {row['job_link'] or 'No job URL found'}")
    print(f"Seen: {row['seen_count']} time(s)")
    print(f"First seen: {row['first_seen_at']}")
    print(f"Last seen: {row['last_seen_at']}")
    print(f"Added to Beacon: {row['created_at']}")
    print(f"Updated in Beacon: {row['updated_at']}")
    print(f"Source: {row['source_email'] or 'Unknown'}")
    print(f"Why: {row['explanation']}")
    return 0


def set_job_status(job_id: int, status: str) -> int:
    """Update the workflow status for one stored job."""

    connection = initialize_storage()
    try:
        updated = update_job_status(connection, job_id, status)
    except ValueError as error:
        connection.close()
        print(error)
        return 2
    connection.close()

    if not updated:
        print(f"No job found with id {job_id}.")
        return 1

    print(f"Updated job {job_id} status to {status}.")
    return 0


def cleanup_non_jobs(apply: bool = False) -> int:
    """Preview or delete stored rows that are clearly not job opportunities."""

    connection = initialize_storage()
    rows = [row for row in fetch_all_jobs(connection) if is_obvious_non_job_row(row)]

    if not rows:
        connection.close()
        print("No obvious non-job rows found.")
        return 0

    action = "Deleting" if apply else "Would delete"
    print(f"{action} {len(rows)} obvious non-job row(s):")
    for row in rows:
        print(f"- {row['id']}: {_console_text(row['company'])} - {_console_text(row['title'])}")
        if apply:
            delete_job_by_id(connection, row["id"])

    connection.close()
    if not apply:
        print("Run `python -m beacon.main cleanup-non-jobs --apply` to delete them.")
    return 0


def cleanup_skipped_jobs(apply: bool = False) -> int:
    """Preview or delete all stored rows categorized as Skip."""

    connection = initialize_storage()
    rows = [row for row in fetch_all_jobs(connection) if row["category"] == "Skip"]

    if not rows:
        connection.close()
        print("No skipped job rows found.")
        return 0

    action = "Deleting" if apply else "Would delete"
    print(f"{action} {len(rows)} skipped job row(s):")
    for row in rows:
        print(
            f"- {row['id']}: "
            f"[{row['score']}] "
            f"{_console_text(row['company'])} - "
            f"{_console_text(row['title'])}"
        )
        if apply:
            delete_job_by_id(connection, row["id"])

    connection.close()
    if not apply:
        print("Run `python -m beacon.main cleanup-skipped --apply` to delete them.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface for local Beacon commands."""

    parser = argparse.ArgumentParser(
        prog="beacon",
        description="Beacon local proof of concept.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "run-local",
        help="Run the local fixture-based Beacon pipeline.",
    )
    subparsers.add_parser(
        "run-gmail",
        help="Run the Beacon pipeline against Gmail IMAP using .env.",
    )
    subparsers.add_parser(
        "list-gmail-mailboxes",
        help="List Gmail IMAP mailbox names for label configuration.",
    )
    subparsers.add_parser(
        "list-jobs",
        help="List stored jobs from SQLite.",
    )
    digest_parser = subparsers.add_parser(
        "digest",
        help="Show recent high-priority jobs from SQLite.",
    )
    digest_parser.add_argument(
        "--since-hours",
        type=int,
        default=24,
        help="Only include jobs added within this many hours.",
    )
    digest_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of jobs to show.",
    )
    digest_parser.add_argument(
        "--include-investigate",
        action="store_true",
        help="Include Investigate jobs in addition to Apply now jobs.",
    )
    show_parser = subparsers.add_parser(
        "show-job",
        help="Show details for one stored job.",
    )
    show_parser.add_argument("job_id", type=int)
    status_parser = subparsers.add_parser(
        "update-status",
        help="Update a stored job status.",
    )
    status_parser.add_argument("job_id", type=int)
    status_parser.add_argument("status")
    cleanup_parser = subparsers.add_parser(
        "cleanup-non-jobs",
        help="Preview or delete obvious non-job rows from SQLite.",
    )
    cleanup_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete the previewed rows.",
    )
    skipped_cleanup_parser = subparsers.add_parser(
        "cleanup-skipped",
        help="Preview or delete every row categorized as Skip.",
    )
    skipped_cleanup_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete the previewed skipped rows.",
    )
    return parser


def main() -> int:
    """CLI entry point used by `python -m beacon.main` and future `beacon`."""

    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command in (None, "run-local"):
            return run_local()
        if args.command == "run-gmail":
            return run_gmail()
        if args.command == "list-gmail-mailboxes":
            return list_gmail_mailboxes()
        if args.command == "list-jobs":
            return list_jobs()
        if args.command == "digest":
            return digest_jobs(
                since_hours=args.since_hours,
                limit=args.limit,
                include_investigate=args.include_investigate,
            )
        if args.command == "show-job":
            return show_job(args.job_id)
        if args.command == "update-status":
            return set_job_status(args.job_id, args.status)
        if args.command == "cleanup-non-jobs":
            return cleanup_non_jobs(apply=args.apply)
        if args.command == "cleanup-skipped":
            return cleanup_skipped_jobs(apply=args.apply)
    except RuntimeError as error:
        print(f"Error: {error}")
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


def _truncate(value: str, max_length: int) -> str:
    """Shorten table cells while keeping output readable."""

    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "."


def _format_table_timestamp(value: str | None) -> str:
    """Format ISO timestamps compactly for the list-jobs table."""

    if not value:
        return "Unknown"

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return _truncate(value, 16)

    return parsed.strftime("%Y-%m-%d %H:%M")


def _parse_stored_timestamp(value: str | None, fallback: datetime) -> datetime:
    """Parse a stored SQLite timestamp and normalize it for time filtering."""

    if not value:
        return fallback

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return fallback

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _render_stored_digest(rows: list[object], since_hours: int, include_investigate: bool) -> str:
    """Render database rows into the concise digest used by the CLI."""

    categories = "Apply now and Investigate" if include_investigate else "Apply now"
    if not rows:
        return f"No {categories} jobs added in the last {since_hours} hour(s)."

    lines = [f"Beacon {categories} digest - last {since_hours} hour(s)", ""]
    for rank, row in enumerate(rows, 1):
        link = row["job_link"] or "No job URL found"
        lines.append(f"{rank}. [{row['score']}] #{row['id']} {row['company']} - {row['title']}")
        lines.append(f"   Added: {_format_table_timestamp(row['created_at'])}")
        lines.append(f"   Link: {link}")
        lines.append(f"   Why: {_console_text(row['explanation'])}")
    return "\n".join(_console_text(line) for line in lines)


def _console_text(value: object) -> str:
    """Return text that the active terminal encoding can print safely."""

    text = str(value)
    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding)


if __name__ == "__main__":
    raise SystemExit(main())
