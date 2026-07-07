from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .compensation import format_salary_estimate
from .canonical_job_resolver import check_search_providers, resolve_canonical_job_url, resolve_source_job_url
from .dedupe import dedupe_jobs
from .digest import render_digest
from .email_filter import is_likely_job_email, is_obvious_non_job_row
from .email_sources import EmailSource, GmailImapEmailSource, LocalFixtureEmailSource
from .employment import infer_employment_type
from .job_description_fetcher import fetch_job_description
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
    update_job_canonical_url,
    update_job_description,
    update_job_status,
    upsert_scored_jobs,
)
from .telegram_notifier import fetch_telegram_updates, load_telegram_settings, send_telegram_message


TELEGRAM_OFFSET_PATH = Path("data/telegram_update_offset.txt")


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


def run_cycle(
    since_hours: int = 48,
    telegram_limit: int = 5,
    include_investigate: bool = False,
    minimum_score: int | None = None,
    max_seen_count: int = 3,
    fetch_descriptions: bool = True,
    description_limit: int = 5,
    description_timeout: int = 20,
    poll_telegram_replies: bool = True,
    poll_limit: int = 20,
    poll_timeout: int = 0,
) -> int:
    """Run the repeatable automation loop for scheduled Beacon scans.

    This is the command to put on a two-hour timer. It runs the Gmail ingestion
    pipeline, refreshes stored rows with the latest scoring/expiry rules, sends
    a concise Telegram digest for newly seen opportunities, and optionally
    checks Telegram for status replies such as `/applied 123`.
    """

    print("Beacon run cycle")
    print("Step 1/5: scan Gmail")
    result = run_gmail()
    if result != 0:
        return result

    print()
    print("Step 2/5: refresh stored scores")
    result = rescore_stored_jobs(apply=True)
    if result != 0:
        return result

    print()
    if fetch_descriptions:
        print("Step 3/5: fetch full job descriptions")
        result = fetch_job_descriptions(
            limit=description_limit,
            include_investigate=include_investigate,
            force=False,
            timeout=description_timeout,
        )
        if result != 0:
            return result
        print()
    else:
        print("Step 3/5: skip full job descriptions")
        print()

    print("Step 4/5: send Telegram digest")
    result = send_telegram_digest(
        since_hours=since_hours,
        limit=telegram_limit,
        include_investigate=include_investigate,
        minimum_score=minimum_score,
        max_seen_count=max_seen_count,
    )
    if result != 0:
        return result

    if not poll_telegram_replies:
        return 0

    print()
    print("Step 5/5: poll Telegram replies")
    return poll_telegram(limit=poll_limit, timeout=poll_timeout)


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


def list_jobs(debug: bool = False) -> int:
    """Print stored jobs in either clean review mode or debug mode."""

    connection = initialize_storage()
    rows = fetch_all_jobs(connection)
    connection.close()

    if not rows:
        print("No stored jobs found. Run `python -m beacon.main run-local` first.")
        return 0

    if debug:
        _print_jobs_debug_table(rows)
    else:
        _print_jobs_clean_table(rows)
    return 0


def _print_jobs_clean_table(rows: list[object]) -> None:
    """Print the default table for human review."""

    print("ID   Score  Action  Status  Desc  Added        Company        Title")
    print("---  -----  ------  ------  ----  -----------  -------------  -----")
    for row in rows:
        print(
            f"{row['id']:<3}  "
            f"{row['score']:<5}  "
            f"{_format_table_category(row['category']):<6}  "
            f"{_format_table_status(row['status']):<6}  "
            f"{_format_description_status(row):<4}  "
            f"{_format_table_timestamp(row['first_seen_at']):<11}  "
            f"{_truncate(_console_text(row['company']), 13):<13}  "
            f"{_truncate(_console_text(row['title']), 92)}"
        )


def _print_jobs_debug_table(rows: list[object]) -> None:
    """Print a wider table with parsing, enrichment, and freshness signals."""

    print("ID   Sc  Sal      Type      Cat    St      Desc  Seen  Exp  Posted  Added        Company        Title")
    print("---  --  -------  --------  -----  ------  ----  ----  ---  ------  -----------  -------------  -----")
    for row in rows:
        print(
            f"{row['id']:<3}  "
            f"{row['score']:<2}  "
            f"{_format_table_salary(row['salary_estimate']):<7}  "
            f"{_format_table_employment_type(row):<8}  "
            f"{_format_table_category(row['category']):<5}  "
            f"{_format_table_status(row['status']):<6}  "
            f"{_format_description_status(row):<4}  "
            f"{row['seen_count']:<4}  "
            f"{_yes_no_short(row['is_expired']):<3}  "
            f"{_format_posted_age(row['posted_date']):<6}  "
            f"{_format_table_timestamp(row['first_seen_at']):<11}  "
            f"{_truncate(_console_text(row['company']), 13):<13}  "
            f"{_truncate(_console_text(row['title']), 76)}"
        )


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


def send_telegram_digest(
    since_hours: int = 48,
    limit: int = 5,
    include_investigate: bool = False,
    minimum_score: int | None = None,
    now: datetime | None = None,
    max_seen_count: int = 3,
) -> int:
    """Send a concise Telegram digest of high-priority stored jobs."""

    rows = _recent_digest_rows(
        since_hours=since_hours,
        limit=limit,
        include_investigate=include_investigate,
        minimum_score=minimum_score,
        now=now,
        max_seen_count=max_seen_count,
    )
    message = _render_telegram_digest(
        rows,
        since_hours=since_hours,
        include_investigate=include_investigate,
        minimum_score=minimum_score,
        max_seen_count=max_seen_count,
    )
    send_telegram_message(message)
    print(f"Sent Telegram digest with {len(rows)} job(s).")
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
    print(f"Source URL: {row['source_url'] or row['job_link'] or 'No source URL found'}")
    print(f"Canonical URL: {row['canonical_url'] or 'Unknown'}")
    print(f"Description fetched: {_yes_no(row['job_description'])}")
    print(f"Description status: {row['description_status'] or 'Unknown'}")
    print(f"Description source: {row['description_source'] or 'Unknown'}")
    if row["job_description_fetched_at"]:
        print(f"Description fetched at: {row['job_description_fetched_at']}")
    if row["job_description_error"]:
        print(f"Description fetch error: {row['job_description_error']}")
    print(f"Seen: {row['seen_count']} time(s)")
    print(f"First seen: {row['first_seen_at']}")
    print(f"Last seen: {row['last_seen_at']}")
    print(f"Added to Beacon: {row['created_at']}")
    print(f"Updated in Beacon: {row['updated_at']}")
    print(f"Source: {row['source_email'] or 'Unknown'}")
    print(f"Why: {row['explanation']}")
    if row["job_description"]:
        print()
        print("Description:")
        print(_console_text(row["job_description"]))
    return 0


def review_descriptions(limit: int = 10, show_text: bool = False, chars: int = 600) -> int:
    """Print a compact review of fetched job descriptions."""

    connection = initialize_storage()
    rows = [
        row
        for row in fetch_all_jobs(connection)
        if row["job_description"] and str(row["job_description"]).strip()
    ][:limit]
    connection.close()

    if not rows:
        print("No fetched job descriptions found.")
        return 0

    print(f"Reviewing {len(rows)} fetched job description(s).")
    for index, row in enumerate(rows, start=1):
        description = _console_text(row["job_description"])
        preview = _description_preview(description, chars)
        print()
        print(f"{index}. {row['id']}: {row['company']} - {row['title']}")
        print(f"   Source: {row['description_source'] or 'Unknown'}")
        print(f"   URL: {row['job_description_url'] or row['canonical_url'] or row['source_url'] or row['job_link'] or 'Unknown'}")
        print(f"   Length: {len(description)} chars")
        print("   Text:")
        if show_text:
            print(preview)
        else:
            print(f"   {_indent_wrapped_preview(preview)}")
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


def fetch_job_descriptions(
    limit: int = 5,
    include_investigate: bool = False,
    force: bool = False,
    timeout: int = 20,
) -> int:
    """Fetch full posting text for stored jobs before AI reasoning."""

    connection = initialize_storage()
    rows = [
        row
        for row in fetch_all_jobs(connection)
        if _should_fetch_job_description(row, include_investigate=include_investigate, force=force)
    ][:limit]

    if not rows:
        connection.close()
        print("No job descriptions need fetching.")
        return 0

    print(f"Fetching descriptions for {len(rows)} job(s).")
    success_count = 0
    for row in rows:
        description_url, description_source = _description_fetch_target(row)
        if not description_url:
            update_job_description(
                connection=connection,
                job_id=row["id"],
                description=None,
                final_url=row["source_url"] or row["job_link"],
                error="LinkedIn alert URL has no canonical posting URL.",
                description_status="linkedin_blocked",
                description_source="linkedin_alert_only",
            )
            print(
                f"- {row['id']}: blocked for {row['company']} - {row['title']} "
                "(linkedin_blocked)"
            )
            continue

        result = fetch_job_description(description_url, timeout=timeout)
        update_job_description(
            connection=connection,
            job_id=row["id"],
            description=result.description,
            final_url=result.final_url,
            error=result.error,
            description_status="fetched" if result.description else "fetch_failed",
            description_source=description_source,
        )
        if result.description:
            success_count += 1
            print(f"- {row['id']}: fetched {len(result.description)} chars for {row['company']} - {row['title']}")
        else:
            print(f"- {row['id']}: failed for {row['company']} - {row['title']} ({result.error})")

    connection.close()
    print(f"Stored {success_count} fetched description(s).")
    return 0


def resolve_canonical_urls(
    limit: int = 10,
    include_investigate: bool = False,
    force: bool = False,
) -> int:
    """Resolve LinkedIn alert jobs to canonical company/ATS posting URLs."""

    connection = initialize_storage()
    rows = [
        row
        for row in fetch_all_jobs(connection)
        if _should_resolve_canonical_url(row, include_investigate=include_investigate, force=force)
    ][:limit]

    if not rows:
        connection.close()
        print("No canonical URLs need resolving.")
        return 0

    print(f"Resolving canonical URLs for {len(rows)} job(s).")
    resolved_count = 0
    for row in rows:
        canonical_url = resolve_canonical_job_url(
            company=row["company"],
            title=row["title"],
            location=row["location"],
        )
        if not canonical_url:
            print(f"- {row['id']}: no canonical URL found for {row['company']} - {row['title']}")
            continue

        update_job_canonical_url(connection, row["id"], canonical_url)
        resolved_count += 1
        print(f"- {row['id']}: {canonical_url}")

    connection.close()
    print(f"Stored {resolved_count} canonical URL(s).")
    return 0


def test_search_provider(query: str = '"Cohere" "Applied AI Engineer" careers') -> int:
    """Run a small live query against configured canonical-search providers."""

    checks = check_search_providers(query)
    if not checks:
        print(
            "No search providers configured. Set SERPER_API_KEY, SERPAPI_API_KEY, "
            "BRAVE_SEARCH_API_KEY, or both GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_ENGINE_ID."
        )
        return 1

    print(f"Testing search providers with query: {query}")
    any_ok = False
    for check in checks:
        if check.ok:
            any_ok = True
            print(f"- {check.provider}: ok ({check.result_count} result(s))")
        else:
            print(f"- {check.provider}: failed ({check.error})")
    return 0 if any_ok else 1


def poll_telegram(limit: int = 20, timeout: int = 0) -> int:
    """Poll Telegram for status commands and update stored jobs."""

    settings = load_telegram_settings()
    updates = fetch_telegram_updates(
        settings=settings,
        offset=_read_telegram_offset(),
        limit=limit,
        timeout=timeout,
    )
    if not updates:
        print("No Telegram updates found.")
        return 0

    processed_count = 0
    max_update_id: int | None = None
    for update in updates:
        update_id = _telegram_update_id(update)
        if update_id is not None:
            max_update_id = update_id if max_update_id is None else max(max_update_id, update_id)

        message = update.get("message")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        if not isinstance(chat, dict) or str(chat.get("id")) != settings.chat_id:
            continue
        text = message.get("text")
        if not isinstance(text, str):
            continue

        response = _handle_telegram_command(text)
        if response:
            send_telegram_message(response, settings=settings)
            processed_count += 1

    if max_update_id is not None:
        _write_telegram_offset(max_update_id + 1)

    print(f"Processed {processed_count} Telegram command(s).")
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
    cycle_parser = subparsers.add_parser(
        "run-cycle",
        help="Run Gmail, rescore stored jobs, send Telegram digest, and poll replies.",
    )
    cycle_parser.add_argument(
        "--since-hours",
        type=int,
        default=48,
        help="Only send jobs first seen within this many hours.",
    )
    cycle_parser.add_argument(
        "--telegram-limit",
        type=int,
        default=5,
        help="Maximum number of jobs to send to Telegram.",
    )
    cycle_parser.add_argument(
        "--include-investigate",
        action="store_true",
        help="Include Investigate jobs in addition to Apply now jobs.",
    )
    cycle_parser.add_argument(
        "--minimum-score",
        type=int,
        default=None,
        help="Optionally only send jobs with at least this score.",
    )
    cycle_parser.add_argument(
        "--max-seen-count",
        type=int,
        default=3,
        help="Only send jobs seen fewer times than this number.",
    )
    cycle_parser.add_argument(
        "--skip-description-fetch",
        action="store_true",
        help="Do not fetch full job descriptions during the cycle.",
    )
    cycle_parser.add_argument(
        "--description-limit",
        type=int,
        default=5,
        help="Maximum number of job descriptions to fetch during the cycle.",
    )
    cycle_parser.add_argument(
        "--description-timeout",
        type=int,
        default=20,
        help="HTTP timeout per job description fetch in seconds.",
    )
    cycle_parser.add_argument(
        "--skip-telegram-poll",
        action="store_true",
        help="Do not poll Telegram for status replies after sending the digest.",
    )
    cycle_parser.add_argument(
        "--poll-limit",
        type=int,
        default=20,
        help="Maximum number of Telegram updates to fetch.",
    )
    cycle_parser.add_argument(
        "--poll-timeout",
        type=int,
        default=0,
        help="Telegram long-poll timeout in seconds.",
    )
    subparsers.add_parser(
        "list-gmail-mailboxes",
        help="List Gmail IMAP mailbox names for label configuration.",
    )
    list_jobs_parser = subparsers.add_parser(
        "list-jobs",
        help="List stored jobs from SQLite.",
    )
    list_jobs_parser.add_argument(
        "--debug",
        action="store_true",
        help="Show a wider diagnostic table with enrichment and parsing signals.",
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
    telegram_parser = subparsers.add_parser(
        "send-telegram-digest",
        help="Send recent high-priority jobs to Telegram.",
    )
    telegram_parser.add_argument(
        "--since-hours",
        type=int,
        default=48,
        help="Only include jobs added within this many hours.",
    )
    telegram_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of jobs to send.",
    )
    telegram_parser.add_argument(
        "--include-investigate",
        action="store_true",
        help="Include Investigate jobs in addition to Apply now jobs.",
    )
    telegram_parser.add_argument(
        "--minimum-score",
        type=int,
        default=None,
        help="Optionally only send jobs with at least this score.",
    )
    telegram_parser.add_argument(
        "--max-seen-count",
        type=int,
        default=3,
        help="Only send jobs seen fewer times than this number.",
    )
    poll_telegram_parser = subparsers.add_parser(
        "poll-telegram",
        help="Poll Telegram for job status commands.",
    )
    poll_telegram_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of Telegram updates to fetch.",
    )
    poll_telegram_parser.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="Telegram long-poll timeout in seconds.",
    )
    show_parser = subparsers.add_parser(
        "show-job",
        help="Show details for one stored job.",
    )
    show_parser.add_argument("job_id", type=int)
    review_descriptions_parser = subparsers.add_parser(
        "review-descriptions",
        help="Review fetched job descriptions without dumping every full posting.",
    )
    review_descriptions_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of fetched descriptions to review.",
    )
    review_descriptions_parser.add_argument(
        "--show-text",
        action="store_true",
        help="Print the preview text without compact indentation.",
    )
    review_descriptions_parser.add_argument(
        "--chars",
        type=int,
        default=600,
        help="Maximum description characters to preview per job.",
    )
    status_parser = subparsers.add_parser(
        "update-status",
        help="Update a stored job status.",
    )
    status_parser.add_argument("job_id", type=int)
    status_parser.add_argument("status")
    fetch_descriptions_parser = subparsers.add_parser(
        "fetch-job-descriptions",
        help="Fetch full posting text for stored jobs with URLs.",
    )
    fetch_descriptions_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of job pages to fetch.",
    )
    fetch_descriptions_parser.add_argument(
        "--include-investigate",
        action="store_true",
        help="Fetch Investigate jobs in addition to Apply now jobs.",
    )
    fetch_descriptions_parser.add_argument(
        "--force",
        action="store_true",
        help="Fetch again even if a description was already stored.",
    )
    fetch_descriptions_parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP timeout per job page in seconds.",
    )
    resolve_canonical_parser = subparsers.add_parser(
        "resolve-canonical-urls",
        help="Resolve LinkedIn alert jobs to canonical company/ATS URLs.",
    )
    resolve_canonical_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of jobs to resolve.",
    )
    resolve_canonical_parser.add_argument(
        "--include-investigate",
        action="store_true",
        help="Resolve Investigate jobs in addition to Apply now jobs.",
    )
    resolve_canonical_parser.add_argument(
        "--force",
        action="store_true",
        help="Resolve again even if a canonical URL is already stored.",
    )
    test_search_provider_parser = subparsers.add_parser(
        "test-search-provider",
        help="Check configured canonical-search provider connectivity.",
    )
    test_search_provider_parser.add_argument(
        "--query",
        nargs="+",
        default=['"Cohere" "Applied AI Engineer" careers'],
        help="Search query to send to configured provider(s).",
    )
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
        if args.command == "run-cycle":
            return run_cycle(
                since_hours=args.since_hours,
                telegram_limit=args.telegram_limit,
                include_investigate=args.include_investigate,
                minimum_score=args.minimum_score,
                max_seen_count=args.max_seen_count,
                fetch_descriptions=not args.skip_description_fetch,
                description_limit=args.description_limit,
                description_timeout=args.description_timeout,
                poll_telegram_replies=not args.skip_telegram_poll,
                poll_limit=args.poll_limit,
                poll_timeout=args.poll_timeout,
            )
        if args.command == "list-gmail-mailboxes":
            return list_gmail_mailboxes()
        if args.command == "list-jobs":
            return list_jobs(debug=args.debug)
        if args.command == "digest":
            return digest_jobs(
                since_hours=args.since_hours,
                limit=args.limit,
                include_investigate=args.include_investigate,
            )
        if args.command == "send-telegram-digest":
            return send_telegram_digest(
                since_hours=args.since_hours,
                limit=args.limit,
                include_investigate=args.include_investigate,
                minimum_score=args.minimum_score,
                max_seen_count=args.max_seen_count,
            )
        if args.command == "poll-telegram":
            return poll_telegram(limit=args.limit, timeout=args.timeout)
        if args.command == "show-job":
            return show_job(args.job_id)
        if args.command == "review-descriptions":
            return review_descriptions(
                limit=args.limit,
                show_text=args.show_text,
                chars=args.chars,
            )
        if args.command == "update-status":
            return set_job_status(args.job_id, args.status)
        if args.command == "fetch-job-descriptions":
            return fetch_job_descriptions(
                limit=args.limit,
                include_investigate=args.include_investigate,
                force=args.force,
                timeout=args.timeout,
            )
        if args.command == "resolve-canonical-urls":
            return resolve_canonical_urls(
                limit=args.limit,
                include_investigate=args.include_investigate,
                force=args.force,
            )
        if args.command == "test-search-provider":
            return test_search_provider(query=" ".join(args.query))
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


def _description_preview(value: str, max_chars: int) -> str:
    """Return a readable single-preview chunk from a stored description."""

    compacted = re.sub(r"\s+", " ", value).strip()
    if max_chars <= 0:
        return ""
    return _truncate(compacted, max_chars)


def _indent_wrapped_preview(value: str) -> str:
    """Indent preview continuation lines for compact CLI output."""

    return value.replace("\n", "\n   ")


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


def _format_description_status(row: object) -> str:
    """Show whether Beacon has fetched a full job description."""

    if row["job_description"]:
        return "Y"
    if row["description_status"] == "linkedin_blocked":
        return "Blk"
    if row["job_description_error"]:
        return "Err"
    return "N"


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


def _recent_digest_rows(
    since_hours: int,
    limit: int,
    include_investigate: bool,
    minimum_score: int | None = None,
    now: datetime | None = None,
    max_seen_count: int | None = None,
) -> list[object]:
    """Return stored rows eligible for digest-style output."""

    connection = initialize_storage()
    rows = fetch_all_jobs(connection)
    connection.close()

    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(hours=since_hours)
    visible_categories = ("Apply now", "Investigate") if include_investigate else ("Apply now",)
    return [
        row
        for row in rows
        if row["category"] in visible_categories
        and row["status"] == "New"
        and not row["is_expired"]
        and (minimum_score is None or row["score"] >= minimum_score)
        and (max_seen_count is None or row["seen_count"] < max_seen_count)
        and _parse_stored_timestamp(row["first_seen_at"], fallback=current_time) >= cutoff
    ][:limit]


def _should_fetch_job_description(row: object, include_investigate: bool, force: bool) -> bool:
    """Return whether a stored job is a good candidate for page enrichment."""

    visible_categories = ("Apply now", "Investigate") if include_investigate else ("Apply now",)
    if row["category"] not in visible_categories:
        return False
    if row["status"] != "New":
        return False
    if row["is_expired"]:
        return False
    if not (row["canonical_url"] or row["source_url"] or row["job_link"]):
        return False
    if not force and row["job_description"]:
        return False
    return True


def _should_resolve_canonical_url(row: object, include_investigate: bool, force: bool) -> bool:
    """Return whether a stored row should be searched for a canonical URL."""

    visible_categories = ("Apply now", "Investigate") if include_investigate else ("Apply now",)
    if row["category"] not in visible_categories:
        return False
    if row["status"] != "New":
        return False
    if row["is_expired"]:
        return False
    if row["canonical_url"] and not force:
        return False

    source_url = row["source_url"] or row["job_link"]
    if not source_url:
        return False

    resolution = resolve_source_job_url(source_url)
    return not resolution.should_fetch_description


def _description_fetch_target(row: object) -> tuple[str | None, str]:
    """Return the URL Beacon should fetch for description text and its source."""

    if row["canonical_url"]:
        return row["canonical_url"], "canonical_url"

    source_url = row["source_url"] or row["job_link"]
    resolution = resolve_source_job_url(source_url)
    if not resolution.should_fetch_description:
        return None, resolution.description_source or "linkedin_alert_only"
    return source_url, "source_url"


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


def _render_telegram_digest(
    rows: list[object],
    since_hours: int,
    include_investigate: bool,
    minimum_score: int | None,
    max_seen_count: int | None = None,
) -> str:
    """Render a compact plain-text Telegram job digest."""

    categories = "Apply now and Investigate" if include_investigate else "Apply now"
    filters = []
    if minimum_score is not None:
        filters.append(f"score >= {minimum_score}")
    if max_seen_count is not None:
        filters.append(f"seen < {max_seen_count}")
    filter_text = ""
    if filters:
        filter_text = " with " + " and ".join(filters)
    if not rows:
        return (
            f"Beacon: no {categories} jobs found in the last {since_hours} "
            f"hour(s){filter_text}."
        )

    lines = [
        f"Beacon digest: {len(rows)} {categories} job(s)",
        f"Window: last {since_hours} hour(s){filter_text}",
        "",
    ]
    for rank, row in enumerate(rows, 1):
        link = row["job_link"] or "No job URL found"
        lines.append(
            f"{rank}. [{row['score']}] #{row['id']} "
            f"{row['company']} - {row['title']}"
        )
        lines.append(
            "   "
            f"Posted: {_format_posted_age(row['posted_date'])} | "
            f"Salary: {_format_table_salary(row['salary_estimate'])} | "
            f"Type: {_format_table_employment_type(row)}"
        )
        lines.append(f"   Link: {link}")
        lines.append(f"   Why: {_truncate(_console_text(row['explanation']), 260)}")
        lines.append("")
    return "\n".join(_console_text(line) for line in lines).strip()


def _handle_telegram_command(text: str) -> str | None:
    """Apply supported Telegram commands and return a confirmation message."""

    command = _parse_telegram_status_command(text)
    if command is None:
        if text.strip().startswith("/"):
            return (
                "Unknown command. Use /applied <job_id>, /reviewed <job_id>, "
                "/skipped <job_id>, or /followup <job_id>."
            )
        return None

    job_id, status = command
    connection = initialize_storage()
    try:
        updated = update_job_status(connection, job_id, status)
    except ValueError as error:
        connection.close()
        return str(error)

    row = fetch_job_by_id(connection, job_id) if updated else None
    connection.close()
    if not updated or row is None:
        return f"No job found with id {job_id}."
    return f"Updated #{job_id} to {row['status']}: {row['company']} - {row['title']}"


def _parse_telegram_status_command(text: str) -> tuple[int, str] | None:
    """Parse Telegram status commands such as `/applied 123`."""

    match = re.match(r"^/(?P<command>\w+)(?:@\w+)?\s+(?P<job_id>\d+)\s*$", text.strip())
    if not match:
        return None

    aliases = {
        "new": "new",
        "reviewed": "reviewed",
        "review": "reviewed",
        "applied": "applied",
        "apply": "applied",
        "skipped": "skipped",
        "skip": "skipped",
        "followup": "follow-up needed",
        "follow-up": "follow-up needed",
        "follow": "follow-up needed",
    }
    command = aliases.get(match.group("command").casefold())
    if command is None:
        return None
    return int(match.group("job_id")), command


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


def _read_telegram_offset(path: Path | None = None) -> int | None:
    """Read the next Telegram update offset from disk."""

    active_path = path or TELEGRAM_OFFSET_PATH
    if not active_path.exists():
        return None
    try:
        return int(active_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _write_telegram_offset(offset: int, path: Path | None = None) -> None:
    """Persist the next Telegram update offset."""

    active_path = path or TELEGRAM_OFFSET_PATH
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(str(offset), encoding="utf-8")


def _telegram_update_id(update: object) -> int | None:
    """Return Telegram update id when present."""

    if not isinstance(update, dict):
        return None
    value = update.get("update_id")
    return value if isinstance(value, int) else None


def _console_text(value: object) -> str:
    """Return text that the active terminal encoding can print safely."""

    text = str(value)
    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding)


if __name__ == "__main__":
    raise SystemExit(main())
