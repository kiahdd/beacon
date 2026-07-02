from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from beacon.models import JobOpportunity, ScoredJob
from beacon.storage import (
    fetch_all_jobs,
    fetch_job_by_id,
    initialize_storage,
    job_fingerprint,
    update_job_status,
    upsert_scored_jobs,
)


class StorageTests(unittest.TestCase):
    """Tests for SQLite persistence and database-level deduplication."""

    def test_upserts_scored_jobs_with_default_new_status(self) -> None:
        """A new scored job should be inserted with workflow status `New`."""
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = initialize_storage(Path(temp_dir) / "beacon.db")
            scored_job = _scored_job(company="Cohere", title="Senior Applied AI Engineer")

            processed_count = upsert_scored_jobs(
                [scored_job],
                connection,
                seen_at=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
            )
            rows = fetch_all_jobs(connection)
            connection.close()

        self.assertEqual(processed_count, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "Cohere")
        self.assertEqual(rows[0]["title"], "Senior Applied AI Engineer")
        self.assertEqual(rows[0]["status"], "New")
        self.assertEqual(rows[0]["seen_count"], 1)
        self.assertEqual(rows[0]["score"], 95)
        self.assertEqual(rows[0]["created_at"], "2026-06-29T12:00:00+00:00")
        self.assertEqual(rows[0]["updated_at"], "2026-06-29T12:00:00+00:00")

    def test_repeated_job_updates_seen_count_instead_of_inserting_duplicate(self) -> None:
        """The unique fingerprint prevents duplicates across multiple runs."""
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = initialize_storage(Path(temp_dir) / "beacon.db")
            first = _scored_job(company="Cohere", title="Senior Applied AI Engineer", score=90)
            second = _scored_job(company=" cohere ", title="Senior Applied AI Engineer", score=97)

            upsert_scored_jobs(
                [first],
                connection,
                seen_at=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
            )
            upsert_scored_jobs(
                [second],
                connection,
                seen_at=datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
            )
            rows = fetch_all_jobs(connection)
            connection.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seen_count"], 2)
        self.assertEqual(rows[0]["score"], 97)
        self.assertEqual(rows[0]["first_seen_at"], "2026-06-29T12:00:00+00:00")
        self.assertEqual(rows[0]["last_seen_at"], "2026-06-30T12:00:00+00:00")
        self.assertEqual(rows[0]["created_at"], "2026-06-29T12:00:00+00:00")
        self.assertEqual(rows[0]["updated_at"], "2026-06-30T12:00:00+00:00")

    def test_initialization_migrates_older_databases_with_timestamps(self) -> None:
        """Older local DBs should gain created_at and updated_at automatically."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            connection = initialize_storage(db_path)
            connection.execute("ALTER TABLE jobs DROP COLUMN created_at")
            connection.execute("ALTER TABLE jobs DROP COLUMN updated_at")
            connection.commit()
            connection.close()

            migrated_connection = initialize_storage(db_path)
            columns = {
                row["name"]
                for row in migrated_connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            migrated_connection.close()

        self.assertIn("created_at", columns)
        self.assertIn("updated_at", columns)

    def test_fingerprint_uses_dedupe_identity_fields(self) -> None:
        """Case and whitespace differences should not change the fingerprint."""
        first = _scored_job(company="Cohere", title="Senior Applied AI Engineer")
        second = _scored_job(company=" cohere ", title="Senior Applied AI Engineer")

        self.assertEqual(job_fingerprint(first), job_fingerprint(second))

    def test_fetch_job_by_id_returns_one_job(self) -> None:
        """Stored jobs should be retrievable by their SQLite id."""
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = initialize_storage(Path(temp_dir) / "beacon.db")
            upsert_scored_jobs([_scored_job(company="Cohere", title="Senior Applied AI Engineer")], connection)
            job_id = fetch_all_jobs(connection)[0]["id"]

            row = fetch_job_by_id(connection, job_id)
            missing = fetch_job_by_id(connection, 999)
            connection.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["company"], "Cohere")
        self.assertIsNone(missing)

    def test_fetch_all_jobs_orders_by_category_then_newest_created_at(self) -> None:
        """The review list should show Apply now jobs first, newest first."""
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = initialize_storage(Path(temp_dir) / "beacon.db")

            upsert_scored_jobs(
                [_scored_job(company="Older Apply", title="Senior ML Engineer", category="Apply now")],
                connection,
                seen_at=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
            )
            upsert_scored_jobs(
                [_scored_job(company="Newer Investigate", title="ML Engineer", category="Investigate")],
                connection,
                seen_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
            )
            upsert_scored_jobs(
                [_scored_job(company="Newer Apply", title="Senior AI Engineer", category="Apply now")],
                connection,
                seen_at=datetime(2026, 7, 1, 13, 0, tzinfo=UTC),
            )

            rows = fetch_all_jobs(connection)
            connection.close()

        self.assertEqual([row["company"] for row in rows], ["Newer Apply", "Older Apply", "Newer Investigate"])

    def test_update_job_status_changes_status(self) -> None:
        """Workflow status updates let the CLI track review/application state."""
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = initialize_storage(Path(temp_dir) / "beacon.db")
            upsert_scored_jobs([_scored_job(company="Cohere", title="Senior Applied AI Engineer")], connection)
            job_id = fetch_all_jobs(connection)[0]["id"]

            updated = update_job_status(connection, job_id, "applied")
            row = fetch_job_by_id(connection, job_id)
            connection.close()

        self.assertTrue(updated)
        self.assertEqual(row["status"], "Applied")

    def test_update_job_status_rejects_unknown_status(self) -> None:
        """Only known workflow statuses should be stored."""
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = initialize_storage(Path(temp_dir) / "beacon.db")
            upsert_scored_jobs([_scored_job(company="Cohere", title="Senior Applied AI Engineer")], connection)
            job_id = fetch_all_jobs(connection)[0]["id"]

            with self.assertRaises(ValueError):
                update_job_status(connection, job_id, "maybe later")
            connection.close()


def _scored_job(
    company: str,
    title: str,
    score: int = 95,
    category: str = "Apply now",
) -> ScoredJob:
    job = JobOpportunity(
        company=company,
        title=title,
        location="Remote Canada",
        work_mode="Remote",
        salary_range="CA$180k-250k",
        seniority="Senior",
        required_skills=("LLM", "RAG", "Databricks"),
        preferred_skills=("MLflow",),
        job_link="https://cohere.ai/careers/123456",
        source_email="LinkedIn Jobs",
        posted_date="12 minutes ago",
    )
    return ScoredJob(
        job=job,
        score=score,
        category=category,
        explanation="Strong fit.",
    )


if __name__ == "__main__":
    unittest.main()
