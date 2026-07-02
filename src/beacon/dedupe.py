from __future__ import annotations

from .models import JobOpportunity


def dedupe_jobs(jobs: list[JobOpportunity]) -> tuple[list[JobOpportunity], int]:
    """Remove duplicate jobs while preserving the first seen copy.

    Job alerts often repeat across LinkedIn, company alerts, and recruiter
    emails. For the POC, a normalized key gives us deterministic deduplication
    without needing a database yet.
    """

    deduped: list[JobOpportunity] = []
    seen_keys: set[tuple[str, str, str, str]] = set()

    for job in jobs:
        key = job_identity_key(job)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(job)

    return deduped, len(jobs) - len(deduped)


def job_identity_key(job: JobOpportunity) -> tuple[str, str, str, str]:
    """Build the deterministic key used to decide whether jobs are duplicates."""

    return (
        _normalize(job.company),
        _normalize(job.title),
        _normalize(job.location),
        _normalize(job.job_link),
    )


def _normalize(value: str | None) -> str:
    """Normalize optional text so case and whitespace do not affect matching."""

    return " ".join((value or "").casefold().split())

