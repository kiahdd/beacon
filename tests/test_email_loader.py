from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from beacon.email_loader import load_email_fixtures


class EmailLoaderTests(unittest.TestCase):
    """Tests for turning local `.txt` email fixtures into SourceEmail objects."""

    def test_loads_txt_fixtures_as_source_emails(self) -> None:
        """Loads only `.txt` fixtures, sorted by filename, with parsed headers."""
        # TemporaryDirectory gives the test an isolated fixture folder that is
        # deleted automatically, so the test never depends on real sample files.
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_dir = Path(temp_dir)

            # The files are intentionally written out of order. The loader sorts
            # by filename so runs are deterministic and easy to reason about.
            (fixture_dir / "002_second.txt").write_text(
                "From: Recruiter <recruiter@example.com>\n"
                "Subject: Applied AI opportunity\n"
                "\n"
                "Body for the second email.\n",
                encoding="utf-8",
            )
            (fixture_dir / "001_first.txt").write_text(
                "From: LinkedIn Jobs <jobs-noreply@linkedin.com>\n"
                "Subject: Senior ML Engineer\n"
                "\n"
                "Body for the first email.\n",
                encoding="utf-8",
            )

            # Non-text files should not enter the email pipeline. This lets the
            # fixture directory hold notes or docs without changing test output.
            (fixture_dir / "ignore.md").write_text(
                "From: Not an email fixture\n",
                encoding="utf-8",
            )

            emails = load_email_fixtures(fixture_dir)

        # The first loaded email should be 001_first because sorting happens
        # before parsing. The source_id comes from the filename stem.
        self.assertEqual(len(emails), 2)
        self.assertEqual(emails[0].source_id, "001_first")
        self.assertEqual(emails[0].sender, "LinkedIn Jobs <jobs-noreply@linkedin.com>")
        self.assertEqual(emails[0].subject, "Senior ML Engineer")

        # The body starts after the blank line that separates headers from
        # content, mirroring a simple raw email format.
        self.assertEqual(emails[0].body, "Body for the first email.")
        self.assertIsNone(emails[0].received_at)
        self.assertEqual(emails[1].source_id, "002_second")

    def test_uses_defaults_when_headers_are_missing(self) -> None:
        """Falls back to safe defaults when a fixture omits email headers."""
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_dir = Path(temp_dir)

            # A malformed or minimal fixture should not crash ingestion. Beacon
            # can still preserve the raw body and mark missing metadata clearly.
            (fixture_dir / "email_without_headers.txt").write_text(
                "This fixture has no explicit headers.\n",
                encoding="utf-8",
            )

            emails = load_email_fixtures(fixture_dir)

        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0].source_id, "email_without_headers")

        # Defaults make downstream parser behavior predictable: sender becomes
        # Unknown, and the subject falls back to the filename.
        self.assertEqual(emails[0].sender, "Unknown")
        self.assertEqual(emails[0].subject, "email_without_headers")
        self.assertEqual(emails[0].body, "This fixture has no explicit headers.")

    def test_missing_directory_returns_empty_list(self) -> None:
        """Treats a missing fixture directory as no available local emails."""
        missing_dir = Path("does-not-exist")

        # This keeps local development friendly: a fresh checkout can run the
        # loader before sample fixtures exist and simply get no emails.
        emails = load_email_fixtures(missing_dir)

        self.assertEqual(emails, [])


if __name__ == "__main__":
    unittest.main()
