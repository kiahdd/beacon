from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from beacon.main import (
    cleanup_non_jobs,
    cleanup_skipped_jobs,
    digest_jobs,
    list_jobs,
    normalize_stored_jobs,
    poll_telegram,
    repair_hiring_rows,
    rescore_stored_jobs,
    run_cycle,
    send_telegram_digest,
    set_job_status,
    show_job,
)
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
        self.assertIn("Sal", output)
        self.assertIn("CA$215k", output)
        self.assertIn("Type", output)
        self.assertIn("Posted", output)
        self.assertIn("Exp", output)
        self.assertIn("Added", output)
        self.assertIn("Cohere", output)
        self.assertIn("Senior Applied AI Engineer", output)

    def test_list_jobs_prints_employment_type(self) -> None:
        """`list-jobs` should flag contract roles in the compact table."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(
                db_path,
                company="Scotiabank",
                title="Data Scientist Contract",
                category="Investigate",
                explanation="contract role is less preferred",
            )

            output = _capture_with_db(db_path, list_jobs)

        self.assertIn("Type", output)
        self.assertIn("Contract", output)

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

    def test_digest_hides_expired_jobs(self) -> None:
        """Expired jobs should not appear in action digests."""
        now = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(
                db_path,
                company="Cohere",
                title="Senior Applied AI Engineer",
                seen_at=now - timedelta(hours=2),
                is_expired=True,
            )

            output = _capture_with_db(db_path, digest_jobs, 24, 10, False, now)

        self.assertIn("No Apply now jobs first seen in the last 24 hour", output)
        self.assertNotIn("Cohere - Senior Applied AI Engineer", output)

    def test_send_telegram_digest_sends_recent_high_score_jobs(self) -> None:
        """Telegram digest should send concise high-priority opportunities."""
        now = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(
                db_path,
                company="Cohere",
                title="Senior Applied AI Engineer",
                score=99,
                seen_at=now - timedelta(hours=1),
                posted_date="12 minutes ago",
            )
            _seed_job(
                db_path,
                company="MaybeCo",
                title="Data Scientist",
                score=70,
                category="Investigate",
                seen_at=now - timedelta(hours=1),
            )

            with patch("beacon.main.send_telegram_message") as send_message:
                output = _capture_with_db(db_path, send_telegram_digest, 24, 5, False, 85, now)

        self.assertIn("Sent Telegram digest with 1 job", output)
        message = send_message.call_args.args[0]
        self.assertIn("Beacon digest", message)
        self.assertIn("Cohere - Senior Applied AI Engineer", message)
        self.assertIn("Posted: 12m", message)
        self.assertNotIn("MaybeCo", message)

    def test_send_telegram_digest_can_include_investigate_jobs(self) -> None:
        """Telegram digest can broaden to Investigate jobs when requested."""
        now = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(
                db_path,
                company="MaybeCo",
                title="Data Scientist",
                score=86,
                category="Investigate",
                seen_at=now - timedelta(hours=1),
            )

            with patch("beacon.main.send_telegram_message") as send_message:
                _capture_with_db(db_path, send_telegram_digest, 24, 5, True, 85, now)

        message = send_message.call_args.args[0]
        self.assertIn("Apply now and Investigate", message)
        self.assertIn("MaybeCo - Data Scientist", message)

    def test_run_cycle_runs_automation_steps(self) -> None:
        """`run-cycle` should run the full scheduled Beacon loop."""
        output = io.StringIO()
        with patch("beacon.main.run_gmail", return_value=0) as run_gmail:
            with patch("beacon.main.rescore_stored_jobs", return_value=0) as rescore:
                with patch("beacon.main.send_telegram_digest", return_value=0) as send_digest:
                    with patch("beacon.main.poll_telegram", return_value=0) as poll:
                        with redirect_stdout(output):
                            result = run_cycle(
                                since_hours=2,
                                telegram_limit=3,
                                include_investigate=True,
                                minimum_score=90,
                                poll_limit=7,
                                poll_timeout=1,
                            )

        self.assertEqual(result, 0)
        self.assertIn("Beacon run cycle", output.getvalue())
        run_gmail.assert_called_once_with()
        rescore.assert_called_once_with(apply=True)
        send_digest.assert_called_once_with(
            since_hours=2,
            limit=3,
            include_investigate=True,
            minimum_score=90,
        )
        poll.assert_called_once_with(limit=7, timeout=1)

    def test_run_cycle_can_skip_telegram_polling(self) -> None:
        """Scheduled runs can send alerts without reading Telegram replies."""
        with patch("beacon.main.run_gmail", return_value=0):
            with patch("beacon.main.rescore_stored_jobs", return_value=0):
                with patch("beacon.main.send_telegram_digest", return_value=0):
                    with patch("beacon.main.poll_telegram") as poll:
                        with redirect_stdout(io.StringIO()):
                            result = run_cycle(poll_telegram_replies=False)

        self.assertEqual(result, 0)
        poll.assert_not_called()

    def test_run_cycle_stops_when_gmail_scan_fails(self) -> None:
        """A failed Gmail scan should stop the cycle before sending alerts."""
        with patch("beacon.main.run_gmail", return_value=1):
            with patch("beacon.main.rescore_stored_jobs") as rescore:
                with patch("beacon.main.send_telegram_digest") as send_digest:
                    with redirect_stdout(io.StringIO()):
                        result = run_cycle()

        self.assertEqual(result, 1)
        rescore.assert_not_called()
        send_digest.assert_not_called()

    def test_poll_telegram_updates_job_status(self) -> None:
        """Polling should process Telegram status commands into SQLite updates."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            offset_path = Path(temp_dir) / "telegram_offset.txt"
            job_id = _seed_job(db_path)
            updates = [
                {
                    "update_id": 41,
                    "message": {
                        "chat": {"id": "chat-456"},
                        "text": f"/applied {job_id}",
                    },
                }
            ]

            with patch("beacon.main.TELEGRAM_OFFSET_PATH", offset_path):
                with patch("beacon.main.load_telegram_settings", return_value=_telegram_settings()):
                    with patch("beacon.main.fetch_telegram_updates", return_value=updates):
                        with patch("beacon.main.send_telegram_message") as send_message:
                            output = _capture_with_db(db_path, poll_telegram)

            connection = initialize_storage(db_path)
            row = connection.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.close()
            offset_value = offset_path.read_text(encoding="utf-8")

        self.assertIn("Processed 1 Telegram command", output)
        self.assertEqual(row["status"], "Applied")
        self.assertEqual(offset_value, "42")
        self.assertIn(f"Updated #{job_id} to Applied", send_message.call_args.args[0])

    def test_poll_telegram_ignores_wrong_chat(self) -> None:
        """Messages from another chat should not update local job status."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            offset_path = Path(temp_dir) / "telegram_offset.txt"
            job_id = _seed_job(db_path)
            updates = [
                {
                    "update_id": 5,
                    "message": {
                        "chat": {"id": "someone-else"},
                        "text": f"/applied {job_id}",
                    },
                }
            ]

            with patch("beacon.main.TELEGRAM_OFFSET_PATH", offset_path):
                with patch("beacon.main.load_telegram_settings", return_value=_telegram_settings()):
                    with patch("beacon.main.fetch_telegram_updates", return_value=updates):
                        with patch("beacon.main.send_telegram_message") as send_message:
                            output = _capture_with_db(db_path, poll_telegram)

            connection = initialize_storage(db_path)
            row = connection.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.close()
            offset_value = offset_path.read_text(encoding="utf-8")

        self.assertIn("Processed 0 Telegram command", output)
        self.assertEqual(row["status"], "New")
        send_message.assert_not_called()
        self.assertEqual(offset_value, "6")

    def test_poll_telegram_reports_missing_job(self) -> None:
        """Polling should confirm when a requested job id does not exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            offset_path = Path(temp_dir) / "telegram_offset.txt"
            _seed_job(db_path)
            updates = [
                {
                    "update_id": 8,
                    "message": {
                        "chat": {"id": "chat-456"},
                        "text": "/reviewed 999",
                    },
                }
            ]

            with patch("beacon.main.TELEGRAM_OFFSET_PATH", offset_path):
                with patch("beacon.main.load_telegram_settings", return_value=_telegram_settings()):
                    with patch("beacon.main.fetch_telegram_updates", return_value=updates):
                        with patch("beacon.main.send_telegram_message") as send_message:
                            _capture_with_db(db_path, poll_telegram)

        self.assertIn("No job found with id 999", send_message.call_args.args[0])

    def test_show_job_prints_details(self) -> None:
        """`show-job` should include URL, score, status, and explanation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(db_path)

            output = _capture_with_db(db_path, show_job, job_id)

        self.assertIn("Cohere - Senior Applied AI Engineer", output)
        self.assertIn("Link: https://cohere.ai/careers/123456", output)
        self.assertIn("Employment type:", output)
        self.assertIn("Salary estimation: CA$215k", output)
        self.assertIn("Expired: No", output)
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

    def test_repair_hiring_rows_previews_company_title_fixes(self) -> None:
        """Repair should preview LinkedIn `Company is hiring` parser mistakes."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(
                db_path,
                company="Unknown",
                title="StackAdapt is hiring a Senior/Staff Applied Machine Learning Scientist",
            )

            output = _capture_with_db(db_path, repair_hiring_rows)
            connection = initialize_storage(db_path)
            row = connection.execute("SELECT company, title FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.close()

        self.assertIn("Would repair 1 company-is-hiring", output)
        self.assertIn("-> StackAdapt - Senior/Staff Applied Machine Learning Scientist", output)
        self.assertEqual(row["company"], "Unknown")

    def test_repair_hiring_rows_apply_updates_and_rescores(self) -> None:
        """Applying repair should rewrite stored fields and scoring explanation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(
                db_path,
                company="Unknown",
                title="StackAdapt is hiring a Applied Machine Learning Scientist (Remote)",
            )

            output = _capture_with_db(db_path, repair_hiring_rows, True)
            connection = initialize_storage(db_path)
            row = connection.execute(
                "SELECT company, title, explanation FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            connection.close()

        self.assertIn("Repairing 1 company-is-hiring", output)
        self.assertEqual(row["company"], "StackAdapt")
        self.assertEqual(row["title"], "Applied Machine Learning Scientist")
        self.assertIn("tier A company preference: StackAdapt", row["explanation"])

    def test_normalize_stored_jobs_previews_display_identity_fixes(self) -> None:
        """Normalization should preview messy company/title rows by default."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(
                db_path,
                company="MongoDB",
                title="Senior AI Engineer",
            )
            _force_raw_identity(db_path, job_id, company=" mongodb ", title="senior ai engineer (remote)")

            output = _capture_with_db(db_path, normalize_stored_jobs)
            connection = initialize_storage(db_path)
            row = connection.execute("SELECT company, title FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.close()

        self.assertIn("Would normalize 1 stored job", output)
        self.assertIn("-> MongoDB - Senior AI Engineer", output)
        self.assertEqual(row["company"], " mongodb ")
        self.assertEqual(row["title"], "senior ai engineer (remote)")

    def test_normalize_stored_jobs_apply_updates_and_rescores(self) -> None:
        """Applying normalization should clean older stored rows."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(
                db_path,
                company="MongoDB",
                title="Senior AI Engineer",
            )
            _force_raw_identity(db_path, job_id, company=" mongodb ", title="senior ai engineer (remote)")

            output = _capture_with_db(db_path, normalize_stored_jobs, True)
            connection = initialize_storage(db_path)
            row = connection.execute(
                "SELECT company, title, explanation FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            connection.close()

        self.assertIn("Normalizing 1 stored job", output)
        self.assertEqual(row["company"], "MongoDB")
        self.assertEqual(row["title"], "Senior AI Engineer")
        self.assertIn("tier B company preference: MongoDB", row["explanation"])

    def test_rescore_stored_jobs_previews_latest_scoring_changes(self) -> None:
        """Rescore should preview rows that would change under current rules."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(
                db_path,
                company="Cohere",
                title="Senior Applied AI Engineer",
                posted_date="129 days ago",
            )

            output = _capture_with_db(db_path, rescore_stored_jobs)
            connection = initialize_storage(db_path)
            row = connection.execute("SELECT score, category, is_expired FROM jobs").fetchone()
            connection.close()

        self.assertIn("Would rescore 1 stored job", output)
        self.assertIn("Exp=N -> 0 Skip Exp=Y", output)
        self.assertEqual(row["score"], 99)
        self.assertEqual(row["category"], "Apply now")
        self.assertEqual(row["is_expired"], 0)

    def test_rescore_stored_jobs_apply_updates_latest_scoring_changes(self) -> None:
        """Applying rescore should update expired flags, score, and category."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(
                db_path,
                company="Cohere",
                title="Senior Applied AI Engineer",
                posted_date="129 days ago",
            )

            output = _capture_with_db(db_path, rescore_stored_jobs, True)
            connection = initialize_storage(db_path)
            row = connection.execute(
                "SELECT score, category, is_expired, explanation FROM jobs"
            ).fetchone()
            connection.close()

        self.assertIn("Rescoring 1 stored job", output)
        self.assertEqual(row["score"], 0)
        self.assertEqual(row["category"], "Skip")
        self.assertEqual(row["is_expired"], 1)
        self.assertIn("posting is more than 2 weeks old", row["explanation"])


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
    is_expired: bool = False,
    explanation: str = "Strong fit.",
    posted_date: str | None = None,
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
                    posted_date=posted_date,
                    is_expired=is_expired,
                ),
                score=score,
                category=category,
                explanation=explanation,
            )
        ],
        connection,
        seen_at=seen_at,
    )
    row = connection.execute("SELECT id FROM jobs").fetchone()
    connection.close()
    return row["id"]


def _telegram_settings():
    from beacon.telegram_notifier import TelegramSettings

    return TelegramSettings(bot_token="token-123", chat_id="chat-456")


def _force_raw_identity(db_path: Path, job_id: int, company: str, title: str) -> None:
    """Simulate older rows written before company/title normalization existed."""

    connection = initialize_storage(db_path)
    connection.execute(
        "UPDATE jobs SET company = ?, title = ? WHERE id = ?",
        (company, title, job_id),
    )
    connection.commit()
    connection.close()
