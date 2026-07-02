from __future__ import annotations

import tempfile
import unittest
import imaplib
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

from beacon.config import DEFAULT_GMAIL_SETTINGS, GmailImapSettings
from beacon.email_sources import GmailEmailSource, GmailImapEmailSource, LocalFixtureEmailSource


class EmailSourceTests(unittest.TestCase):
    """Tests for email source adapters used by the Beacon pipeline."""

    def test_local_fixture_email_source_loads_source_emails(self) -> None:
        """LocalFixtureEmailSource should adapt fixture files to SourceEmail."""
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_dir = Path(temp_dir)
            (fixture_dir / "001_sample.txt").write_text(
                "From: LinkedIn Jobs <jobs-noreply@linkedin.com>\n"
                "Subject: Senior ML Engineer\n"
                "\n"
                "Email body.\n",
                encoding="utf-8",
            )
            source = LocalFixtureEmailSource(fixture_dir)

            emails = source.load_emails()

        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0].source_id, "001_sample")
        self.assertEqual(emails[0].subject, "Senior ML Engineer")

    def test_gmail_email_source_fails_clearly_until_implemented(self) -> None:
        """The Gmail adapter should advertise that OAuth is not wired yet."""
        source = GmailEmailSource(DEFAULT_GMAIL_SETTINGS)

        with self.assertRaises(NotImplementedError) as error:
            source.load_emails()

        self.assertIn("Gmail integration is not configured yet", str(error.exception))

    def test_gmail_imap_source_reads_messages(self) -> None:
        """GmailImapEmailSource should normalize RFC822 messages from IMAP."""
        message = EmailMessage()
        message["From"] = "LinkedIn Jobs <jobs-noreply@linkedin.com>"
        message["Subject"] = "Senior ML Engineer at Shopify"
        message["Date"] = "Mon, 29 Jun 2026 09:00:00 -0400"
        message.set_content("Company: Shopify\nRole: Senior ML Engineer\n")
        fake_imap = FakeImap([message.as_bytes()])
        source = GmailImapEmailSource(
            settings=GmailImapSettings(
                address="person@example.com",
                app_password="app-password",
                max_results=5,
            ),
            imap_factory=lambda host: fake_imap,
            verbose=False,
        )

        emails = source.load_emails()

        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0].source_id, "gmail-imap:INBOX:1")
        self.assertEqual(emails[0].subject, "Senior ML Engineer at Shopify")
        self.assertEqual(emails[0].sender, "LinkedIn Jobs <jobs-noreply@linkedin.com>")
        self.assertIn("Company: Shopify", emails[0].body)
        self.assertTrue(fake_imap.logged_in)

    def test_gmail_imap_source_skips_non_job_messages_before_full_download(self) -> None:
        """Header filtering should avoid fetching full bodies for obvious noise."""
        non_job = EmailMessage()
        non_job["From"] = "Newsletter <news@example.com>"
        non_job["Subject"] = "Weekly digest"
        non_job["Date"] = "Mon, 29 Jun 2026 08:00:00 -0400"
        non_job.set_content("This body mentions data scientist but the headers are not job-like.")

        job = EmailMessage()
        job["From"] = "LinkedIn Jobs <jobs-noreply@linkedin.com>"
        job["Subject"] = "Senior ML Engineer at Shopify"
        job["Date"] = "Mon, 29 Jun 2026 09:00:00 -0400"
        job.set_content("Company: Shopify\nRole: Senior ML Engineer\n")

        fake_imap = FakeImap([non_job.as_bytes(), job.as_bytes()])
        source = GmailImapEmailSource(
            settings=GmailImapSettings(
                address="person@example.com",
                app_password="app-password",
                max_results=5,
            ),
            imap_factory=lambda host: fake_imap,
            verbose=False,
        )

        emails = source.load_emails()

        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0].subject, "Senior ML Engineer at Shopify")
        self.assertEqual(fake_imap.full_fetch_count, 1)
        self.assertEqual(fake_imap.header_fetch_count, 2)

    def test_gmail_imap_source_scans_labels_before_inbox_and_dedupes(self) -> None:
        """Configured label mailboxes should be scanned before the inbox."""
        label_message = EmailMessage()
        label_message["From"] = "LinkedIn Jobs <jobs-noreply@linkedin.com>"
        label_message["Subject"] = "Senior Data Scientist at Empire Life"
        label_message["Date"] = "Mon, 29 Jun 2026 09:00:00 -0400"
        label_message["Message-ID"] = "<same-message@example.com>"
        label_message.set_content("Company: Empire Life\nRole: Senior Data Scientist\n")

        inbox_duplicate = EmailMessage()
        inbox_duplicate["From"] = "LinkedIn Jobs <jobs-noreply@linkedin.com>"
        inbox_duplicate["Subject"] = "Senior Data Scientist at Empire Life"
        inbox_duplicate["Date"] = "Mon, 29 Jun 2026 09:00:00 -0400"
        inbox_duplicate["Message-ID"] = "<same-message@example.com>"
        inbox_duplicate.set_content("Company: Empire Life\nRole: Senior Data Scientist\n")

        fake_imap = FakeImap(
            {
                "jobs-linkedin-job-alerts": [label_message.as_bytes()],
                "INBOX": [inbox_duplicate.as_bytes()],
            }
        )
        source = GmailImapEmailSource(
            settings=GmailImapSettings(
                address="person@example.com",
                app_password="app-password",
                label_mailboxes=("jobs-linkedin-job-alerts",),
            ),
            imap_factory=lambda host: fake_imap,
            verbose=False,
        )

        emails = source.load_emails()

        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0].source_id, "gmail-imap:<same-message@example.com>")
        self.assertEqual(fake_imap.selected_mailboxes, ["jobs-linkedin-job-alerts", "INBOX"])

    def test_gmail_imap_source_requires_credentials(self) -> None:
        """The IMAP adapter should fail before connecting when secrets are absent."""
        source = GmailImapEmailSource(
            settings=GmailImapSettings(address=None, app_password=None),
            imap_factory=lambda host: FakeImap([]),
            verbose=False,
        )

        with self.assertRaises(RuntimeError) as error:
            source.load_emails()

        self.assertIn("Gmail IMAP credentials are missing", str(error.exception))

    def test_gmail_imap_source_reports_login_failures(self) -> None:
        """Bad Gmail credentials should produce a user-readable error."""
        source = GmailImapEmailSource(
            settings=GmailImapSettings(
                address="person@example.com",
                app_password="wrong-password",
            ),
            imap_factory=lambda host: FakeImap([], fail_login=True),
            verbose=False,
        )

        with self.assertRaises(RuntimeError) as error:
            source.load_emails()

        self.assertIn("Could not log in to Gmail IMAP", str(error.exception))

    def test_gmail_imap_source_lists_mailboxes(self) -> None:
        """The source should expose Gmail label/mailbox names for configuration."""
        source = GmailImapEmailSource(
            settings=GmailImapSettings(
                address="person@example.com",
                app_password="app-password",
            ),
            imap_factory=lambda host: FakeImap(
                {
                    "INBOX": [],
                    "jobs-linkedin-job-alerts": [],
                }
            ),
            verbose=False,
        )

        mailboxes = source.list_mailboxes()

        self.assertIn("INBOX", mailboxes)
        self.assertIn("jobs-linkedin-job-alerts", mailboxes)


class FakeImap:
    """Tiny IMAP test double that mimics the methods Beacon calls."""

    def __init__(self, messages: list[bytes] | dict[str, list[bytes]], fail_login: bool = False) -> None:
        self.messages_by_mailbox = messages if isinstance(messages, dict) else {"INBOX": messages}
        self.fail_login = fail_login
        self.logged_in = False
        self.full_fetch_count = 0
        self.header_fetch_count = 0
        self.selected_mailbox = "INBOX"
        self.selected_mailboxes: list[str] = []

    def __enter__(self) -> "FakeImap":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def login(self, address: str, password: str) -> tuple[str, list[bytes]]:
        if self.fail_login:
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials")
        self.logged_in = bool(address and password)
        return "OK", []

    def list(self) -> tuple[str, list[bytes]]:
        responses = [
            f'(\\HasNoChildren) "/" "{mailbox}"'.encode()
            for mailbox in self.messages_by_mailbox
        ]
        return "OK", responses

    def select(self, mailbox: str) -> tuple[str, list[bytes]]:
        mailbox = mailbox.strip('"')
        if mailbox not in self.messages_by_mailbox:
            return "NO", []
        self.selected_mailbox = mailbox
        self.selected_mailboxes.append(mailbox)
        return "OK", []

    def search(self, charset: None, criteria: str) -> tuple[str, list[bytes]]:
        messages = self.messages_by_mailbox[self.selected_mailbox]
        message_ids = b" ".join(str(index).encode() for index in range(1, len(messages) + 1))
        return "OK", [message_ids]

    def fetch(self, message_id: bytes, query: str) -> tuple[str, list[tuple[bytes, bytes]]]:
        index = int(message_id.decode()) - 1
        messages = self.messages_by_mailbox[self.selected_mailbox]
        if "HEADER.FIELDS" in query:
            self.header_fetch_count += 1
            return "OK", [(b"HEADER", _headers_only(messages[index]))]

        self.full_fetch_count += 1
        return "OK", [(b"RFC822", messages[index])]


def _headers_only(raw_message: bytes) -> bytes:
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    header_message = EmailMessage()
    for header in ("From", "Subject", "Date", "Message-ID"):
        if message[header]:
            header_message[header] = str(message[header])
    return header_message.as_bytes()


if __name__ == "__main__":
    unittest.main()
