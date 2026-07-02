from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta

from .compensation import format_salary_estimate
from .dedupe import dedupe_jobs
from .digest import render_digest
from .email_filter import is_likely_job_email, is_obvious_non_job_row
from .email_sources import EmailSource, GmailImapEmailSource, LocalFixtureEmailSource
from .employment import infer_employment_type
from .models import JobOpportunity
from .normalization import normalize_company, normalize_title
from .parser import parse_email
from .scorer import score_job
from .storage import (
    delete_job_by_id,
    fetch_all_jobs,
    fetch_job_by_id,
    initialize_storage,
    update_scored_job_by_id,
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

    print("ID   Sc  Sal      Type      Cat    St      Seen  Exp  Posted  Added        Company        Title")
    print("---  --  -------  --------  -----  ------  ----  ---  ------  -----------  -------------  -----")
    for row in rows:
        print(
            f"{row['id']:<3}  "
            f"{row['score']:<2}  "
            f"{_format_table_salary(row['salary_estimate']):<7}  "
            f"{_format_table_employment_type(row):<8}  "
            f"{_format_table_category(row['category']):<5}  "
            f"{_format_table_status(row['status']):<6}  "
            f"{row['seen_count']:<4}  "
            f"{_yes_no_short(row['is_expired']):<3}  "
            f"{_format_posted_age(row['posted_date']):<6}  "
            f"{_format_table_timestamp(row['first_seen_at']):<11}  "
            f"{_truncate(_console_text(row['company']), 13):<13}  "
            f"{_truncate(_console_text(row['title']), 76)}"
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
        and not row["is_expired"]
        and _parse_stored_timestamp(row["first_seen_at"], fallback=current_time) >= cutoff
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
    print(f"Employment type: {_employment_type_from_row(row)}")
    print(f"Salary: {row['salary_range'] or 'Unknown'}")
    print(f"Salary estimation: {format_salary_estimate(row['salary_estimate'])}")
    print(f"Expired: {_yes_no(row['is_expired'])}")
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


def repair_hiring_rows(apply: bool = False) -> int:
    """Preview or repair stored rows shaped like `Company is hiring a Role`."""

    connection = initialize_storage()
    rows = []
    for row in fetch_all_jobs(connection):
        repair = _company_is_hiring_repair(row["title"])
        if repair:
            rows.append((row, repair))

    if not rows:
        connection.close()
        print("No company-is-hiring rows found.")
        return 0

    action = "Repairing" if apply else "Would repair"
    print(f"{action} {len(rows)} company-is-hiring row(s):")
    for row, repair in rows:
        company, title = repair
        print(_console_text(
            f"- {row['id']}: "
            f"{_console_text(row['company'])} - {_console_text(row['title'])} "
            f"-> {company} - {title}"
        ))
        if apply:
            update_scored_job_by_id(
                connection,
                row["id"],
                score_job(_job_from_row_with_updates(row, company=company, title=title)),
            )

    connection.close()
    if not apply:
        print("Run `python -m beacon.main repair-hiring-rows --apply` to update them.")
    return 0


def normalize_stored_jobs(apply: bool = False) -> int:
    """Preview or normalize company/title fields for existing stored rows."""

    connection = initialize_storage()
    rows = []
    for row in fetch_all_jobs(connection):
        company = normalize_company(row["company"])
        title = normalize_title(row["title"])
        if company != row["company"] or title != row["title"]:
            rows.append((row, company, title))

    if not rows:
        connection.close()
        print("No stored jobs need company/title normalization.")
        return 0

    action = "Normalizing" if apply else "Would normalize"
    print(f"{action} {len(rows)} stored job row(s):")
    for row, company, title in rows:
        print(_console_text(
            f"- {row['id']}: "
            f"{_console_text(row['company'])} - {_console_text(row['title'])} "
            f"-> {company} - {title}"
        ))
        if apply:
            update_scored_job_by_id(
                connection,
                row["id"],
                score_job(_job_from_row_with_updates(row, company=company, title=title)),
            )

    connection.close()
    if not apply:
        print("Run `python -m beacon.main normalize-stored-jobs --apply` to update them.")
    return 0


def rescore_stored_jobs(apply: bool = False) -> int:
    """Preview or apply the latest scoring rules to every stored job."""

    connection = initialize_storage()
    rows = fetch_all_jobs(connection)
    changes = []
    for row in rows:
        rescored = score_job(_job_from_row_with_updates(row, company=row["company"], title=row["title"]))
        if _stored_score_differs(row, rescored):
            changes.append((row, rescored))

    if not changes:
        connection.close()
        print("No stored jobs need rescoring.")
        return 0

    action = "Rescoring" if apply else "Would rescore"
    print(f"{action} {len(changes)} stored job row(s):")
    for row, rescored in changes:
        print(_console_text(
            f"- {row['id']}: "
            f"{row['score']} {row['category']} Exp={_yes_no_short(row['is_expired'])} "
            f"-> {rescored.score} {rescored.category} Exp={_yes_no_short(rescored.job.is_expired)} "
            f"{rescored.job.company} - {rescored.job.title}"
        ))
        if apply:
            update_scored_job_by_id(connection, row["id"], rescored)

    connection.close()
    if not apply:
        print("Run `python -m beacon.main rescore-stored-jobs --apply` to update them.")
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
    hiring_repair_parser = subparsers.add_parser(
        "repair-hiring-rows",
        help="Preview or repair stored rows like `Company is hiring a Role`.",
    )
    hiring_repair_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually repair the previewed rows.",
    )
    normalize_parser = subparsers.add_parser(
        "normalize-stored-jobs",
        help="Preview or normalize stored company/title display fields.",
    )
    normalize_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually normalize the previewed rows.",
    )
    rescore_parser = subparsers.add_parser(
        "rescore-stored-jobs",
        help="Preview or apply the latest scoring rules to stored jobs.",
    )
    rescore_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually rescore the previewed rows.",
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
        if args.command == "repair-hiring-rows":
            return repair_hiring_rows(apply=args.apply)
        if args.command == "normalize-stored-jobs":
            return normalize_stored_jobs(apply=args.apply)
        if args.command == "rescore-stored-jobs":
            return rescore_stored_jobs(apply=args.apply)
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
        return "-"

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return _truncate(value, 11)

    return parsed.strftime("%m-%d %H:%M")


def _format_posted_age(value: str | None) -> str:
    """Format posted-age text into compact table values."""

    if not value:
        return "-"

    text = str(value).strip().casefold().rstrip(".")
    if text in ("unknown", "none", ""):
        return "-"
    if text == "today":
        return "today"

    match = re.search(r"(?P<count>\d+)\s*(?P<unit>minute|minutes|hour|hours|day|days|week|weeks|month|months)", text)
    if not match:
        return _truncate(str(value).strip(), 6)

    count = match.group("count")
    unit = match.group("unit")
    if unit.startswith("minute"):
        return f"{count}m"
    if unit.startswith("hour"):
        return f"{count}h"
    if unit.startswith("day"):
        return f"{count}d"
    if unit.startswith("week"):
        return f"{count}w"
    if unit.startswith("month"):
        return f"{count}mo"
    return _truncate(str(value).strip(), 6)


def _format_table_salary(value: object) -> str:
    """Format salary estimates compactly and hide unknown values."""

    formatted = format_salary_estimate(value)
    return "-" if formatted == "Unknown" else formatted


def _format_table_employment_type(row: object) -> str:
    """Format employment type compactly and hide unknown values."""

    employment_type = _employment_type_from_row(row)
    if employment_type == "Unknown":
        return "-"
    return employment_type


def _format_table_category(value: str) -> str:
    """Abbreviate action categories for compact table output."""

    aliases = {
        "Apply now": "Apply",
        "Investigate": "Inv",
        "Skip": "Skip",
    }
    return aliases.get(value, _truncate(value, 5))


def _format_table_status(value: str) -> str:
    """Abbreviate workflow status for compact table output."""

    aliases = {
        "New": "New",
        "Reviewed": "Review",
        "Applied": "Appl",
        "Skipped": "Skip",
        "Follow-up needed": "Follow",
    }
    return aliases.get(value, _truncate(value, 6))


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
        return f"No {categories} jobs first seen in the last {since_hours} hour(s)."

    lines = [f"Beacon {categories} digest - last {since_hours} hour(s)", ""]
    for rank, row in enumerate(rows, 1):
        link = row["job_link"] or "No job URL found"
        lines.append(f"{rank}. [{row['score']}] #{row['id']} {row['company']} - {row['title']}")
        lines.append(f"   First seen: {_format_table_timestamp(row['first_seen_at'])}")
        lines.append(f"   Link: {link}")
        lines.append(f"   Why: {_console_text(row['explanation'])}")
    return "\n".join(_console_text(line) for line in lines)


def _employment_type_from_row(row: object) -> str:
    """Infer employment type from stored row fields used by the CLI."""

    return infer_employment_type(
        [
            row["title"],
            row["seniority"],
            row["work_mode"],
            row["salary_range"],
            row["required_skills_json"],
            row["preferred_skills_json"],
            row["explanation"],
        ]
    )


def _stored_score_differs(row: object, rescored: object) -> bool:
    """Return whether rescoring would change a stored row."""

    return (
        row["score"] != rescored.score
        or row["category"] != rescored.category
        or bool(row["is_expired"]) != rescored.job.is_expired
        or row["company"] != rescored.job.company
        or row["title"] != rescored.job.title
        or row["explanation"] != rescored.explanation
    )


def _company_is_hiring_repair(title: str) -> tuple[str, str] | None:
    """Return corrected company/title for stored `Company is hiring...` titles."""

    title_pattern = (
        r"(?:Senior/Staff\s+|Senior\s+|Staff\s+)?(?:Applied\s+)?(?:Data Scientist|"
        r"Machine Learning Scientist|Machine Learning Engineer|ML Scientist|"
        r"ML Engineer|AI Engineer|Applied AI Engineer)"
    )
    match = re.search(
        rf"(?P<company>[A-Z][A-Za-z0-9&.' -]+?)\s+is hiring\s+(?:a |an )?"
        rf"(?P<title>{title_pattern})(?:\s*\([^)]*\))?",
        title,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group("company").strip(), match.group("title").strip()


def _job_from_row_with_updates(row: object, company: str, title: str) -> JobOpportunity:
    """Build a JobOpportunity from a stored row while replacing parsed fields."""

    return JobOpportunity(
        company=company,
        title=title,
        location=row["location"],
        work_mode=row["work_mode"],
        salary_range=row["salary_range"],
        seniority=row["seniority"],
        required_skills=tuple(json.loads(row["required_skills_json"])),
        preferred_skills=tuple(json.loads(row["preferred_skills_json"])),
        job_link=row["job_link"],
        source_email=row["source_email"],
        posted_date=row["posted_date"],
        is_expired=bool(row["is_expired"]),
    )


def _yes_no(value: object) -> str:
    """Format SQLite booleans for compact CLI output."""

    return "Yes" if bool(value) else "No"


def _yes_no_short(value: object) -> str:
    """Format booleans for dense table output."""

    return "Y" if bool(value) else "N"


def _console_text(value: object) -> str:
    """Return text that the active terminal encoding can print safely."""

    text = str(value)
    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding)


if __name__ == "__main__":
    raise SystemExit(main())
