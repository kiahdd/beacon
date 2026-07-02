from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .compensation import estimate_salary
from .dedupe import job_identity_key
from .models import ScoredJob
from .normalization import normalize_scored_job


DEFAULT_DB_PATH = Path("data/beacon.db")
VALID_JOB_STATUSES = ("New", "Reviewed", "Applied", "Skipped", "Follow-up needed")


def initialize_storage(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Prepare local SQLite storage and return an open connection.

    The database enforces cross-run deduplication with a unique job fingerprint.
    If Beacon sees the same job again days or weeks later, storage updates
    `last_seen_at` and `seen_count` instead of inserting a duplicate row.
    """

    # `parents=True` allows the whole path to be created from a fresh checkout.
    # `exist_ok=True` makes repeated local runs safe.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    _create_schema(connection)
    _migrate_schema(connection)
    return connection


def upsert_scored_jobs(
    scored_jobs: list[ScoredJob],
    connection: sqlite3.Connection,
    seen_at: datetime | None = None,
) -> int:
    """Insert or update scored jobs and return how many jobs were processed."""

    timestamp = (seen_at or datetime.now(UTC)).isoformat()
    for scored_job in scored_jobs:
        _upsert_scored_job(normalize_scored_job(scored_job), connection, timestamp)
    connection.commit()
    return len(scored_jobs)


def fetch_all_jobs(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return stored jobs in the order Kiana should review them.

    The list is workflow-first: jobs Beacon says to apply to appear before
    investigation rows, skipped rows sink to the bottom, and each group shows
    newest first-seen opportunities first.
    """

    return list(
        connection.execute(
            """
            SELECT *
            FROM jobs
            ORDER BY
                CASE category
                    WHEN 'Apply now' THEN 0
                    WHEN 'Investigate' THEN 1
                    WHEN 'Skip' THEN 2
                    ELSE 3
                END,
                first_seen_at DESC,
                score DESC
            """
        )
    )


def fetch_job_by_id(connection: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    """Return one stored job by database id."""

    return connection.execute(
        """
        SELECT *
        FROM jobs
        WHERE id = ?
        """,
        (job_id,),
    ).fetchone()


def delete_job_by_id(connection: sqlite3.Connection, job_id: int) -> bool:
    """Delete one stored job by id and return whether a row changed."""

    cursor = connection.execute(
        """
        DELETE FROM jobs
        WHERE id = ?
        """,
        (job_id,),
    )
    connection.commit()
    return cursor.rowcount > 0


def update_job_status(connection: sqlite3.Connection, job_id: int, status: str) -> bool:
    """Update a job workflow status and return whether a row changed."""

    normalized_status = _normalize_status(status)
    if normalized_status not in VALID_JOB_STATUSES:
        allowed = ", ".join(VALID_JOB_STATUSES)
        raise ValueError(f"Unknown status '{status}'. Allowed statuses: {allowed}")

    timestamp = datetime.now(UTC).isoformat()
    cursor = connection.execute(
        """
        UPDATE jobs
        SET status = ?, updated_at = ?
        WHERE id = ?
        """,
        (normalized_status, timestamp, job_id),
    )
    connection.commit()
    return cursor.rowcount > 0


def update_scored_job_by_id(
    connection: sqlite3.Connection,
    job_id: int,
    scored_job: ScoredJob,
) -> bool:
    """Replace parsed/scored fields for one existing row and refresh fingerprint."""

    scored_job = normalize_scored_job(scored_job)
    job = scored_job.job
    fingerprint = job_fingerprint(scored_job)
    timestamp = datetime.now(UTC).isoformat()
    cursor = connection.execute(
        """
        UPDATE jobs
        SET
            job_fingerprint = ?,
            company = ?,
            title = ?,
            location = ?,
            work_mode = ?,
            salary_range = ?,
            salary_estimate = ?,
            seniority = ?,
            required_skills_json = ?,
            preferred_skills_json = ?,
            job_link = ?,
            source_email = ?,
            posted_date = ?,
            is_expired = ?,
            score = ?,
            category = ?,
            explanation = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            fingerprint,
            job.company,
            job.title,
            job.location,
            job.work_mode,
            job.salary_range,
            estimate_salary(job.salary_range),
            job.seniority,
            json.dumps(job.required_skills),
            json.dumps(job.preferred_skills),
            job.job_link,
            job.source_email,
            job.posted_date,
            int(job.is_expired),
            scored_job.score,
            scored_job.category,
            scored_job.explanation,
            timestamp,
            job_id,
        ),
    )
    connection.commit()
    return cursor.rowcount > 0


def job_fingerprint(scored_job: ScoredJob) -> str:
    """Create a stable fingerprint from the same fields used for dedupe."""

    scored_job = normalize_scored_job(scored_job)
    key_text = "|".join(job_identity_key(scored_job.job))
    return hashlib.sha256(key_text.encode("utf-8")).hexdigest()


def _normalize_status(status: str) -> str:
    """Normalize CLI-friendly status text into the stored display value."""

    aliases = {
        "new": "New",
        "reviewed": "Reviewed",
        "applied": "Applied",
        "skipped": "Skipped",
        "skip": "Skipped",
        "follow-up needed": "Follow-up needed",
        "followup needed": "Follow-up needed",
        "follow-up": "Follow-up needed",
        "followup": "Follow-up needed",
    }
    return aliases.get(status.strip().casefold(), status.strip())


def _create_schema(connection: sqlite3.Connection) -> None:
    """Create the local jobs table if it does not already exist."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_fingerprint TEXT NOT NULL UNIQUE,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1,
            company TEXT NOT NULL,
            title TEXT NOT NULL,
            location TEXT,
            work_mode TEXT,
            salary_range TEXT,
            salary_estimate INTEGER,
            seniority TEXT,
            required_skills_json TEXT NOT NULL,
            preferred_skills_json TEXT NOT NULL,
            job_link TEXT,
            source_email TEXT,
            posted_date TEXT,
            is_expired INTEGER NOT NULL DEFAULT 0,
            score INTEGER NOT NULL,
            category TEXT NOT NULL,
            explanation TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'New',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def _migrate_schema(connection: sqlite3.Connection) -> None:
    """Add newer columns when an older local database already exists.

    `CREATE TABLE IF NOT EXISTS` leaves existing tables untouched. This tiny
    migration keeps Kiana's local `data/beacon.db` compatible as Beacon evolves.
    """

    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
    }
    timestamp = datetime.now(UTC).isoformat()

    if "created_at" not in existing_columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN created_at TEXT")
        connection.execute(
            """
            UPDATE jobs
            SET created_at = COALESCE(first_seen_at, last_seen_at, ?)
            WHERE created_at IS NULL
            """,
            (timestamp,),
        )

    if "updated_at" not in existing_columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN updated_at TEXT")
        connection.execute(
            """
            UPDATE jobs
            SET updated_at = COALESCE(last_seen_at, first_seen_at, ?)
            WHERE updated_at IS NULL
            """,
            (timestamp,),
        )

    if "is_expired" not in existing_columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN is_expired INTEGER NOT NULL DEFAULT 0")

    if "salary_estimate" not in existing_columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN salary_estimate INTEGER")
        for row in connection.execute("SELECT id, salary_range FROM jobs").fetchall():
            connection.execute(
                """
                UPDATE jobs
                SET salary_estimate = ?
                WHERE id = ?
                """,
                (estimate_salary(row["salary_range"]), row["id"]),
            )

    connection.commit()


def _upsert_scored_job(
    scored_job: ScoredJob,
    connection: sqlite3.Connection,
    timestamp: str,
) -> None:
    """Persist one scored job, updating sightings when the fingerprint exists."""

    scored_job = normalize_scored_job(scored_job)
    job = scored_job.job
    fingerprint = job_fingerprint(scored_job)
    connection.execute(
        """
        INSERT INTO jobs (
            job_fingerprint,
            first_seen_at,
            last_seen_at,
            seen_count,
            company,
            title,
            location,
            work_mode,
            salary_range,
            salary_estimate,
            seniority,
            required_skills_json,
            preferred_skills_json,
            job_link,
            source_email,
            posted_date,
            is_expired,
            score,
            category,
            explanation,
            status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'New', ?, ?)
        ON CONFLICT(job_fingerprint) DO UPDATE SET
            last_seen_at = excluded.last_seen_at,
            seen_count = jobs.seen_count + 1,
            company = excluded.company,
            title = excluded.title,
            location = excluded.location,
            work_mode = excluded.work_mode,
            salary_range = excluded.salary_range,
            salary_estimate = excluded.salary_estimate,
            seniority = excluded.seniority,
            required_skills_json = excluded.required_skills_json,
            preferred_skills_json = excluded.preferred_skills_json,
            job_link = excluded.job_link,
            source_email = excluded.source_email,
            posted_date = excluded.posted_date,
            is_expired = excluded.is_expired,
            score = excluded.score,
            category = excluded.category,
            explanation = excluded.explanation,
            updated_at = excluded.updated_at
        """,
        (
            fingerprint,
            timestamp,
            timestamp,
            job.company,
            job.title,
            job.location,
            job.work_mode,
            job.salary_range,
            estimate_salary(job.salary_range),
            job.seniority,
            json.dumps(job.required_skills),
            json.dumps(job.preferred_skills),
            job.job_link,
            job.source_email,
            job.posted_date,
            int(job.is_expired),
            scored_job.score,
            scored_job.category,
            scored_job.explanation,
            timestamp,
            timestamp,
        ),
    )
