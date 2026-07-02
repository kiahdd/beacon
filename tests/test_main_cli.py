from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from beacon.main import cleanup_non_jobs, cleanup_skipped_jobs, digest_jobs, list_jobs, set_job_status, show_job
from beacon.models import JobOpportunity, ScoredJob
from beacon.storage import initialize_storage, upsert_scored_jobs


class MainCliTests(unittest.TestCase):
    """Tests for user-facing database inspection commands."""

    def test_list_jobs_prints_stored_jobs(self) -> None:
        """`list-jobs` should show a compact table of saved opportunities."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(db_path)

            output = _capture_with_db(db_path, list_jobs)

        self.assertIn("ID", output)
        self.assertIn("Posted", output)
        self.assertIn("Added", output)
        self.assertIn("Cohere", output)
        self.assertIn("Senior Applied AI Engineer", output)

    def test_list_jobs_handles_non_ascii_titles(self) -> None:
        """Real emails can contain symbols that Windows terminals cannot print."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(db_path, title="Senior AI Engineer 📝")

            output = _capture_with_db(db_path, list_jobs)

        self.assertIn("Senior AI Engineer", output)

    def test_digest_shows_recent_apply_now_jobs(self) -> None:
        """`digest` should focus on recent Apply now jobs by default."""
        now = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(
                db_path,
                company="Cohere",
                title="Senior Applied AI Engineer",
                seen_at=now - timedelta(hours=2),
            )
            _seed_job(
                db_path,
                company="OldCo",
                title="Senior Data Scientist",
                seen_at=now - timedelta(days=3),
            )
            _seed_job(
                db_path,
                company="MaybeCo",
                title="Data Scientist",
                category="Investigate",
                seen_at=now - timedelta(hours=1),
            )

            output = _capture_with_db(db_path, digest_jobs, 24, 10, False, now)

        self.assertIn("Beacon Apply now digest", output)
        self.assertIn("Cohere - Senior Applied AI Engineer", output)
        self.assertNotIn("OldCo", output)
        self.assertNotIn("MaybeCo", output)

    def test_digest_can_include_recent_investigate_jobs(self) -> None:
        """`--include-investigate` should broaden the digest when reviewing."""
        now = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(
                db_path,
                company="MaybeCo",
                title="Data Scientist",
                category="Investigate",
                seen_at=now - timedelta(hours=1),
            )

            output = _capture_with_db(db_path, digest_jobs, 24, 10, True, now)

        self.assertIn("Apply now and Investigate", output)
        self.assertIn("MaybeCo - Data Scientist", output)

    def test_show_job_prints_details(self) -> None:
        """`show-job` should include URL, score, status, and explanation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(db_path)

            output = _capture_with_db(db_path, show_job, job_id)

        self.assertIn("Cohere - Senior Applied AI Engineer", output)
        self.assertIn("Link: https://cohere.ai/careers/123456", output)
        self.assertIn("Posted:", output)
        self.assertIn("Added to Beacon:", output)
        self.assertIn("Updated in Beacon:", output)
        self.assertIn("Why: Strong fit.", output)

    def test_update_status_prints_confirmation(self) -> None:
        """`update-status` should update the row and confirm the command."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(db_path)

            output = _capture_with_db(db_path, set_job_status, job_id, "reviewed")
            connection = initialize_storage(db_path)
            row = connection.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.close()

        self.assertIn(f"Updated job {job_id}", output)
        self.assertEqual(row["status"], "Reviewed")

    def test_cleanup_non_jobs_previews_obvious_noise(self) -> None:
        """Cleanup should preview obvious inbox noise without deleting by default."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(db_path, title="Security alert", score=13, category="Skip")

            output = _capture_with_db(db_path, cleanup_non_jobs)
            connection = initialize_storage(db_path)
            row = connection.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.close()

        self.assertIn("Would delete 1 obvious non-job", output)
        self.assertIsNotNone(row)

    def test_cleanup_non_jobs_apply_deletes_obvious_noise(self) -> None:
        """Cleanup should delete previewed non-job rows only with --apply."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(db_path, title="Security alert", score=13, category="Skip")

            output = _capture_with_db(db_path, cleanup_non_jobs, True)
            connection = initialize_storage(db_path)
            row = connection.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.close()

        self.assertIn("Deleting 1 obvious non-job", output)
        self.assertIsNone(row)

    def test_cleanup_skipped_jobs_previews_all_skips(self) -> None:
        """Skipped cleanup should preview all Skip rows without deleting by default."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(db_path, title="Junior Analyst", score=12, category="Skip")

            output = _capture_with_db(db_path, cleanup_skipped_jobs)
            connection = initialize_storage(db_path)
            row = connection.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.close()

        self.assertIn("Would delete 1 skipped job", output)
        self.assertIsNotNone(row)

    def test_cleanup_skipped_jobs_apply_deletes_all_skips(self) -> None:
        """Skipped cleanup should delete Skip rows only with --apply."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(db_path, title="Junior Analyst", score=12, category="Skip")

            output = _capture_with_db(db_path, cleanup_skipped_jobs, True)
            connection = initialize_storage(db_path)
            row = connection.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.close()

        self.assertIn("Deleting 1 skipped job", output)
        self.assertIsNone(row)


def _capture_with_db(db_path: Path, function, *args) -> str:
    output = io.StringIO()
    with patch("beacon.main.initialize_storage", lambda: initialize_storage(db_path)):
        with redirect_stdout(output):
            function(*args)
    return output.getvalue()


def _seed_job(
    db_path: Path,
    company: str = "Cohere",
    title: str = "Senior Applied AI Engineer",
    score: int = 99,
    category: str = "Apply now",
    seen_at: datetime | None = None,
) -> int:
    connection = initialize_storage(db_path)
    upsert_scored_jobs(
        [
            ScoredJob(
                job=JobOpportunity(
                    company=company,
                    title=title,
                    location="Remote Canada",
                    work_mode="Remote",
                    salary_range="CA$180k-250k",
                    seniority="Senior",
                    job_link="https://cohere.ai/careers/123456",
                ),
                score=score,
                category=category,
                explanation="Strong fit.",
            )
        ],
        connection,
        seen_at=seen_at,
    )
    row = connection.execute("SELECT id FROM jobs").fetchone()
    connection.close()
    return row["id"]
