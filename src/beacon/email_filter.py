from __future__ import annotations

import os
from pathlib import Path
from sqlite3 import Row

from .env_loader import load_env_file
from .models import SourceEmail


JOB_SOURCE_MARKERS = (
    "linkedin",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "indeed",
    "glassdoor",
    "wellfound",
    "workday",
    "smartrecruiters",
    "workable",
    "jobs-noreply",
    "careers",
    "recruiter",
)

JOB_TEXT_MARKERS = (
    "job alert",
    "jobs you may be interested",
    "is hiring",
    "we're hiring",
    "we are hiring",
    "hiring a",
    "hiring an",
    "recruiter",
    "role:",
    "company:",
    "apply:",
    "application",
    "contract opportunity",
    "job opportunity",
    "recruiting opportunity",
    "career",
    "senior data scientist",
    "staff data scientist",
    "data scientist",
    "machine learning engineer",
    "ml engineer",
    "applied ai",
    "ai engineer",
    "mlops",
)

OBVIOUS_NON_JOB_MARKERS = (
    "security alert",
    "verification code",
    "google account was recovered",
    "receipt",
    "payment",
    "balance for",
    "a table you requested",
    "room rental",
    "fifa fan festival",
    "hydrosols",
    "spotify",
    "meetup",
    "ai tinkerers",
    "happening today",
    "thanks for joining us",
    "open to connecting",
    "survey opportunity",
    "paid survey",
    "maiy thai",
    "work from office",
    "thank you for applying",
    "thank you for your application",
    "thanks for applying",
    "application received",
    "virtual interview",
    "just messaged you",
    "messaged you",
)


def is_likely_job_email(email: SourceEmail) -> bool:
    """Return whether an email is worth sending into the job parser.

    The parser is intentionally willing to extract from messy text. This filter
    protects it from unrelated inbox messages such as receipts or security
    alerts, which otherwise become low-score "Unknown" jobs.
    """

    sender = email.sender.casefold()
    subject = email.subject.casefold()
    body = email.body.casefold()
    searchable_text = f"{subject}\n{body[:5000]}"

    if any(marker in searchable_text for marker in OBVIOUS_NON_JOB_MARKERS):
        return False

    if _matches_known_recruiter(sender):
        return True

    if any(marker in sender for marker in JOB_SOURCE_MARKERS):
        return True

    return any(marker in searchable_text for marker in JOB_TEXT_MARKERS)


def is_obvious_non_job_row(row: Row) -> bool:
    """Return whether an existing stored row is clearly inbox noise."""

    if row["category"] != "Skip":
        return False

    text = f"{row['company']} {row['title']}".casefold()
    return any(marker in text for marker in OBVIOUS_NON_JOB_MARKERS)


def _matches_known_recruiter(sender: str) -> bool:
    """Return whether the sender matches recruiter allowlist values from `.env`."""

    load_env_file(Path(".env"))
    known_values = _env_list("KNOWN_RECRUITER_NAMES") + _env_list("KNOWN_RECRUITER_EMAILS")
    return any(value in sender for value in known_values)


def _env_list(name: str) -> tuple[str, ...]:
    """Parse comma-separated env values into lowercase match tokens."""

    raw_value = os.environ.get(name, "")
    return tuple(value.strip().casefold() for value in raw_value.split(",") if value.strip())
