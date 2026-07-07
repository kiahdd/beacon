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
    fetch_job_descriptions,
    list_jobs,
    normalize_stored_jobs,
    poll_telegram,
    repair_hiring_rows,
    review_descriptions,
    resolve_canonical_urls,
    rescore_stored_jobs,
    run_cycle,
    send_telegram_digest,
    set_job_status,
    show_job,
    test_search_provider,
)
from beacon.canonical_job_resolver import SearchProviderCheck
from beacon.models import JobOpportunity, ScoredJob
from beacon.storage import initialize_storage, upsert_scored_jobs


class MainCliTests(unittest.TestCase):
    """Tests for user-facing database inspection commands."""

    def test_list_jobs_prints_stored_jobs(self) -> None:
        """`list-jobs` should show a clean table of saved opportunities."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(db_path, description="Build LLM evaluation systems.")

            output = _capture_with_db(db_path, list_jobs)

        self.assertIn("ID", output)
        self.assertIn("Action", output)
        self.assertIn("Desc", output)
        self.assertIn("Added", output)
        self.assertIn("Cohere", output)
        self.assertIn("Senior Applied AI Engineer", output)
        self.assertIn("Y", output)
        self.assertNotIn("CA$215k", output)
        self.assertNotIn("Posted", output)

    def test_list_jobs_debug_prints_diagnostic_columns(self) -> None:
        """`list-jobs --debug` should show wider parsing/enrichment signals."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(db_path, description_error="HTTP 403")

            output = _capture_with_db(db_path, list_jobs, True)

        self.assertIn("Sal", output)
        self.assertIn("CA$215k", output)
        self.assertIn("Type", output)
        self.assertIn("Desc", output)
        self.assertIn("Err", output)
        self.assertIn("Posted", output)
        self.assertIn("Exp", output)

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

            output = _capture_with_db(db_path, list_jobs, True)

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

    def test_send_telegram_digest_sends_recent_apply_now_jobs(self) -> None:
        """Telegram digest should use Apply now as the default action filter."""
        now = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(
                db_path,
                company="Cohere",
                title="Senior Applied AI Engineer",
                score=72,
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
                output = _capture_with_db(db_path, send_telegram_digest, 24, 5, False, None, now)

        self.assertIn("Sent Telegram digest with 1 job", output)
        message = send_message.call_args.args[0]
        self.assertIn("Beacon digest", message)
        self.assertIn("Cohere - Senior Applied AI Engineer", message)
        self.assertNotIn("score >=", message)
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
                _capture_with_db(db_path, send_telegram_digest, 24, 5, True, None, now)

        message = send_message.call_args.args[0]
        self.assertIn("Apply now and Investigate", message)
        self.assertIn("MaybeCo - Data Scientist", message)

    def test_send_telegram_digest_hides_jobs_seen_three_or_more_times(self) -> None:
        """Telegram digest should avoid repeating jobs Beacon has seen often."""
        now = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(
                db_path,
                company="FreshCo",
                title="Senior AI Engineer",
                score=92,
                seen_at=now - timedelta(hours=3),
                seen_count=2,
            )
            _seed_job(
                db_path,
                company="RepeatCo",
                title="Senior Data Scientist",
                score=95,
                seen_at=now - timedelta(hours=3),
                seen_count=3,
            )

            with patch("beacon.main.send_telegram_message") as send_message:
                _capture_with_db(db_path, send_telegram_digest, 48, 5, False, None, now)

        message = send_message.call_args.args[0]
        self.assertIn("seen < 3", message)
        self.assertIn("FreshCo - Senior AI Engineer", message)
        self.assertNotIn("RepeatCo", message)

    def test_send_telegram_digest_hides_reviewed_and_applied_jobs(self) -> None:
        """Telegram digest should only resend jobs still marked New."""
        now = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(
                db_path,
                company="FreshCo",
                title="Senior AI Engineer",
                score=92,
                seen_at=now - timedelta(hours=1),
            )
            _seed_job(
                db_path,
                company="ReviewedCo",
                title="Senior Data Scientist",
                score=94,
                seen_at=now - timedelta(hours=1),
                status="Reviewed",
            )
            _seed_job(
                db_path,
                company="AppliedCo",
                title="ML Engineer",
                score=96,
                seen_at=now - timedelta(hours=1),
                status="Applied",
            )

            with patch("beacon.main.send_telegram_message") as send_message:
                _capture_with_db(db_path, send_telegram_digest, 48, 5, False, None, now)

        message = send_message.call_args.args[0]
        self.assertIn("FreshCo - Senior AI Engineer", message)
        self.assertNotIn("ReviewedCo", message)
        self.assertNotIn("AppliedCo", message)

    def test_run_cycle_runs_automation_steps(self) -> None:
        """`run-cycle` should run the full scheduled Beacon loop."""
        output = io.StringIO()
        with patch("beacon.main.run_gmail", return_value=0) as run_gmail:
            with patch("beacon.main.rescore_stored_jobs", return_value=0) as rescore:
                with patch("beacon.main.fetch_job_descriptions", return_value=0) as fetch_descriptions:
                    with patch("beacon.main.send_telegram_digest", return_value=0) as send_digest:
                        with patch("beacon.main.poll_telegram", return_value=0) as poll:
                            with redirect_stdout(output):
                                result = run_cycle(
                                    since_hours=2,
                                    telegram_limit=3,
                                    include_investigate=True,
                                    minimum_score=90,
                                    max_seen_count=4,
                                    description_limit=6,
                                    description_timeout=9,
                                    poll_limit=7,
                                    poll_timeout=1,
                                )

        self.assertEqual(result, 0)
        self.assertIn("Beacon run cycle", output.getvalue())
        run_gmail.assert_called_once_with()
        rescore.assert_called_once_with(apply=True)
        fetch_descriptions.assert_called_once_with(
            limit=6,
            include_investigate=True,
            force=False,
            timeout=9,
        )
        send_digest.assert_called_once_with(
            since_hours=2,
            limit=3,
            include_investigate=True,
            minimum_score=90,
            max_seen_count=4,
        )
        poll.assert_called_once_with(limit=7, timeout=1)

    def test_run_cycle_can_skip_telegram_polling(self) -> None:
        """Scheduled runs can send alerts without reading Telegram replies."""
        with patch("beacon.main.run_gmail", return_value=0):
            with patch("beacon.main.rescore_stored_jobs", return_value=0):
                with patch("beacon.main.fetch_job_descriptions", return_value=0):
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

    def test_run_cycle_can_skip_description_fetching(self) -> None:
        """Scheduled runs can skip page enrichment when speed matters."""
        with patch("beacon.main.run_gmail", return_value=0):
            with patch("beacon.main.rescore_stored_jobs", return_value=0):
                with patch("beacon.main.fetch_job_descriptions") as fetch_descriptions:
                    with patch("beacon.main.send_telegram_digest", return_value=0):
                        with patch("beacon.main.poll_telegram", return_value=0):
                            with redirect_stdout(io.StringIO()):
                                result = run_cycle(fetch_descriptions=False)

        self.assertEqual(result, 0)
        fetch_descriptions.assert_not_called()

    def test_poll_telegram_updates_job_status(self) -> None:
        """Polling should process Telegram status commands into SQLite updates."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            offset_path = Path(temp_dir) / "telegram_offset.txt"
            job_id = _seed_job(
                db_path,
                description="Build LLM evaluation systems.\nWork on RAG and AI agents.",
            )
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
            job_id = _seed_job(
                db_path,
                description="Build LLM evaluation systems.\nWork on RAG and AI agents.",
            )

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
        self.assertIn("Description:", output)
        self.assertIn("Build LLM evaluation systems.", output)
        self.assertIn("Work on RAG and AI agents.", output)

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

    def test_review_descriptions_prints_compact_description_preview(self) -> None:
        """`review-descriptions` should print fetched description previews."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(
                db_path,
                company="Cohere",
                title="Senior Applied AI Engineer",
                description="Build LLM evaluation systems.\nPartner with product teams.",
            )
            connection = initialize_storage(db_path)
            connection.execute(
                """
                UPDATE jobs
                SET job_description_url = ?,
                    description_source = ?
                WHERE id = ?
                """,
                ("https://cohere.ai/careers/123456", "source_url", job_id),
            )
            connection.commit()
            connection.close()

            output = _capture_with_db(db_path, review_descriptions, 5, False, 45)

        self.assertIn("Reviewing 1 fetched job description", output)
        self.assertIn(f"1. {job_id}: Cohere - Senior Applied AI Engineer", output)
        self.assertIn("Source: source_url", output)
        self.assertIn("URL: https://cohere.ai/careers/123456", output)
        self.assertIn("Build LLM evaluation systems. Partner with", output)

    def test_review_descriptions_handles_empty_database(self) -> None:
        """`review-descriptions` should explain when there is nothing to review."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(db_path)

            output = _capture_with_db(db_path, review_descriptions)

        self.assertIn("No fetched job descriptions found.", output)

    def test_fetch_job_descriptions_stores_description_for_new_apply_jobs(self) -> None:
        """Description fetch should enrich only eligible stored jobs."""
        from beacon.job_description_fetcher import JobDescriptionFetchResult

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(db_path, company="Cohere", title="Senior Applied AI Engineer")
            _seed_job(db_path, company="ReviewedCo", title="Senior ML Engineer", status="Reviewed")

            with patch(
                "beacon.main.fetch_job_description",
                return_value=JobDescriptionFetchResult(
                    description="Build LLM evaluation systems.",
                    final_url="https://cohere.ai/careers/123456",
                ),
            ) as fetch_description:
                output = _capture_with_db(db_path, fetch_job_descriptions, 5, False, False, 20)

            connection = initialize_storage(db_path)
            row = connection.execute(
                """
                SELECT job_description, job_description_url, description_status, description_source
                FROM jobs
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            connection.close()

        self.assertIn("Fetching descriptions for 1 job", output)
        self.assertIn("Stored 1 fetched description", output)
        fetch_description.assert_called_once_with("https://cohere.ai/careers/123456", timeout=20)
        self.assertEqual(row["job_description"], "Build LLM evaluation systems.")
        self.assertEqual(row["job_description_url"], "https://cohere.ai/careers/123456")
        self.assertEqual(row["description_status"], "fetched")
        self.assertEqual(row["description_source"], "source_url")

    def test_fetch_job_descriptions_prefers_canonical_url(self) -> None:
        """Description fetch should try canonical URLs before source alert URLs."""
        from beacon.job_description_fetcher import JobDescriptionFetchResult

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(
                db_path,
                company="Instacart",
                title="Senior Data Scientist - Shopping Experience",
                job_link="https://www.linkedin.com/jobs/view/123",
                canonical_url="https://instacart.careers/job/senior-data-scientist",
            )

            with patch(
                "beacon.main.fetch_job_description",
                return_value=JobDescriptionFetchResult(
                    description="Build search ranking models.",
                    final_url="https://instacart.careers/job/senior-data-scientist",
                ),
            ) as fetch_description:
                _capture_with_db(db_path, fetch_job_descriptions, 5, False, False, 20)

            connection = initialize_storage(db_path)
            row = connection.execute(
                """
                SELECT job_description, job_description_url, description_status, description_source
                FROM jobs
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            connection.close()

        fetch_description.assert_called_once_with(
            "https://instacart.careers/job/senior-data-scientist",
            timeout=20,
        )
        self.assertEqual(row["job_description"], "Build search ranking models.")
        self.assertEqual(row["job_description_url"], "https://instacart.careers/job/senior-data-scientist")
        self.assertEqual(row["description_status"], "fetched")
        self.assertEqual(row["description_source"], "canonical_url")

    def test_fetch_job_descriptions_marks_linkedin_rows_without_fetching(self) -> None:
        """LinkedIn alert URLs should be marked blocked without scraping LinkedIn."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(
                db_path,
                company="Instacart",
                title="Senior Data Scientist - Shopping Experience",
                job_link="https://www.linkedin.com/comm/jobs/view/4402666272?trackingId=abc",
            )

            with patch("beacon.main.fetch_job_description") as fetch_description:
                output = _capture_with_db(db_path, fetch_job_descriptions, 5, False, False, 20)

            connection = initialize_storage(db_path)
            row = connection.execute(
                """
                SELECT
                    job_description,
                    job_description_url,
                    job_description_error,
                    description_status,
                    description_source
                FROM jobs
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            connection.close()

        fetch_description.assert_not_called()
        self.assertIn("blocked for Instacart", output)
        self.assertIsNone(row["job_description"])
        self.assertEqual(
            row["job_description_url"],
            "https://www.linkedin.com/comm/jobs/view/4402666272?trackingId=abc",
        )
        self.assertEqual(row["description_status"], "linkedin_blocked")
        self.assertEqual(row["description_source"], "linkedin_alert_only")
        self.assertIn("LinkedIn alert URL", row["job_description_error"])

    def test_resolve_canonical_urls_stores_resolved_url_for_linkedin_rows(self) -> None:
        """Canonical resolver should fill canonical_url for LinkedIn alert rows."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(
                db_path,
                company="Instacart",
                title="Senior Data Scientist - Shopping Experience",
                job_link="https://www.linkedin.com/jobs/view/123",
            )

            with patch(
                "beacon.main.resolve_canonical_job_url",
                return_value="https://instacart.careers/job/senior-data-scientist",
            ) as resolver:
                output = _capture_with_db(db_path, resolve_canonical_urls, 10, False, False)

            connection = initialize_storage(db_path)
            row = connection.execute("SELECT canonical_url FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.close()

        resolver.assert_called_once_with(
            company="Instacart",
            title="Senior Data Scientist - Shopping Experience",
            location="Remote Canada",
        )
        self.assertIn("Stored 1 canonical URL", output)
        self.assertEqual(row["canonical_url"], "https://instacart.careers/job/senior-data-scientist")

    def test_resolve_canonical_urls_skips_non_linkedin_source_urls(self) -> None:
        """Direct company URLs should not be sent through canonical search."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            _seed_job(
                db_path,
                company="Cohere",
                title="Senior Applied AI Engineer",
                job_link="https://cohere.ai/careers/123456",
            )

            with patch("beacon.main.resolve_canonical_job_url") as resolver:
                output = _capture_with_db(db_path, resolve_canonical_urls, 10, False, False)

        resolver.assert_not_called()
        self.assertIn("No canonical URLs need resolving.", output)

    def test_resolve_canonical_urls_respects_existing_canonical_url_unless_forced(self) -> None:
        """Existing canonical URLs should be preserved unless --force is used."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(
                db_path,
                company="Instacart",
                title="Senior Data Scientist",
                job_link="https://www.linkedin.com/jobs/view/123",
                canonical_url="https://instacart.careers/old",
            )

            with patch("beacon.main.resolve_canonical_job_url") as resolver:
                _capture_with_db(db_path, resolve_canonical_urls, 10, False, False)
            resolver.assert_not_called()

            with patch(
                "beacon.main.resolve_canonical_job_url",
                return_value="https://instacart.careers/new",
            ):
                _capture_with_db(db_path, resolve_canonical_urls, 10, False, True)

            connection = initialize_storage(db_path)
            row = connection.execute("SELECT canonical_url FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.close()

        self.assertEqual(row["canonical_url"], "https://instacart.careers/new")

    def test_resolve_canonical_urls_can_include_investigate_jobs(self) -> None:
        """Investigate jobs are only resolved when explicitly included."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "beacon.db"
            job_id = _seed_job(
                db_path,
                company="MaybeCo",
                title="Data Scientist",
                category="Investigate",
                job_link="https://www.linkedin.com/jobs/view/456",
            )

            with patch("beacon.main.resolve_canonical_job_url") as resolver:
                _capture_with_db(db_path, resolve_canonical_urls, 10, False, False)
            resolver.assert_not_called()

            with patch(
                "beacon.main.resolve_canonical_job_url",
                return_value="https://maybe.co/careers/data-scientist",
            ) as resolver:
                _capture_with_db(db_path, resolve_canonical_urls, 10, True, False)

            connection = initialize_storage(db_path)
            row = connection.execute("SELECT canonical_url FROM jobs WHERE id = ?", (job_id,)).fetchone()
            connection.close()

        resolver.assert_called_once()
        self.assertEqual(row["canonical_url"], "https://maybe.co/careers/data-scientist")

    def test_test_search_provider_prints_configured_provider_health(self) -> None:
        with patch(
            "beacon.main.check_search_providers",
            return_value=[
                SearchProviderCheck(provider="serper", ok=True, result_count=3),
                SearchProviderCheck(provider="google", ok=False, error="HTTP 403: disabled"),
            ],
        ) as checker:
            output = _capture_stdout(test_search_provider, "query")

        checker.assert_called_once_with("query")
        self.assertIn("Testing search providers with query: query", output)
        self.assertIn("- serper: ok (3 result(s))", output)
        self.assertIn("- google: failed (HTTP 403: disabled)", output)

    def test_test_search_provider_returns_error_when_none_configured(self) -> None:
        with patch("beacon.main.check_search_providers", return_value=[]):
            output = _capture_stdout(test_search_provider)

        self.assertIn("No search providers configured.", output)

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


def _capture_stdout(function, *args) -> str:
    output = io.StringIO()
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
    seen_count: int = 1,
    status: str = "New",
    description: str | None = None,
    description_error: str | None = None,
    job_link: str = "https://cohere.ai/careers/123456",
    canonical_url: str | None = None,
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
                    job_link=job_link,
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
    row = connection.execute("SELECT id FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
    if seen_count != 1:
        connection.execute("UPDATE jobs SET seen_count = ? WHERE id = ?", (seen_count, row["id"]))
    if status != "New":
        connection.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, row["id"]))
    if description is not None or description_error is not None:
        connection.execute(
            """
            UPDATE jobs
            SET job_description = ?, job_description_error = ?
            WHERE id = ?
            """,
            (description, description_error, row["id"]),
        )
    if canonical_url is not None:
        connection.execute(
            "UPDATE jobs SET canonical_url = ? WHERE id = ?",
            (canonical_url, row["id"]),
        )
    if (
        seen_count != 1
        or status != "New"
        or description is not None
        or description_error is not None
        or canonical_url is not None
    ):
        connection.commit()
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
