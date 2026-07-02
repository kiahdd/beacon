from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SourceEmail:
    """Raw-ish email input after it has entered Beacon.

    The Gmail API will eventually provide richer metadata. For the local POC we
    only keep the fields needed to parse a job alert deterministically.
    """

    source_id: str
    subject: str
    sender: str
    received_at: datetime | None
    body: str


@dataclass(frozen=True)
class JobOpportunity:
    """Normalized job posting extracted from an email.

    Optional fields are allowed because job-alert emails vary a lot. Missing
    salary, location, or apply links should not break ingestion; they simply
    become weaker signals for scoring.
    """

    company: str
    title: str
    location: str | None = None
    work_mode: str | None = None
    salary_range: str | None = None
    seniority: str | None = None
    required_skills: tuple[str, ...] = field(default_factory=tuple)
    preferred_skills: tuple[str, ...] = field(default_factory=tuple)
    job_link: str | None = None
    source_email: str | None = None
    posted_date: str | None = None


@dataclass(frozen=True)
class ScoredJob:
    """A job after fit scoring and categorization."""

    job: JobOpportunity
    score: int
    category: str
    explanation: str
