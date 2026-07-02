from __future__ import annotations

import unittest
from unittest.mock import patch

from beacon.email_filter import is_likely_job_email
from beacon.models import SourceEmail


class EmailFilterTests(unittest.TestCase):
    """Tests for keeping non-job inbox noise out of the parser."""

    def test_accepts_linkedin_job_alerts(self) -> None:
        email = SourceEmail(
            source_id="linkedin",
            subject="Senior Data Scientist at Empire Life",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=None,
            body="Empire Life is hiring a Senior Data Scientist.",
        )

        self.assertTrue(is_likely_job_email(email))

    def test_accepts_recruiter_style_messages(self) -> None:
        email = SourceEmail(
            source_id="recruiter",
            subject="Applied AI opportunity",
            sender="Jane Recruiter <jane@example.com>",
            received_at=None,
            body="We're hiring a Senior Machine Learning Engineer.",
        )

        self.assertTrue(is_likely_job_email(email))

    def test_rejects_security_alerts(self) -> None:
        email = SourceEmail(
            source_id="security",
            subject="Security alert",
            sender="Google <no-reply@accounts.google.com>",
            received_at=None,
            body="A new sign-in was detected.",
        )

        self.assertFalse(is_likely_job_email(email))

    def test_rejects_restaurant_alerts(self) -> None:
        email = SourceEmail(
            source_id="restaurant",
            subject="Alert: A Table You Requested",
            sender="Real Sports <reservations@example.com>",
            received_at=None,
            body="A table is available tonight.",
        )

        self.assertFalse(is_likely_job_email(email))

    def test_rejects_networking_messages_that_only_sound_job_adjacent(self) -> None:
        email = SourceEmail(
            source_id="networking",
            subject="Open to connecting with a Toronto AI engineer building RAG and agentic systems?",
            sender="LinkedIn <messages-noreply@linkedin.com>",
            received_at=None,
            body="Someone viewed your profile and wants to connect.",
        )

        self.assertFalse(is_likely_job_email(email))

    def test_rejects_ai_meetup_reminders(self) -> None:
        email = SourceEmail(
            source_id="meetup",
            subject="Happening today: Toronto Agentic AI Meetup with Google",
            sender="Meetup <info@meetup.com>",
            received_at=None,
            body="Join us tonight for talks and networking.",
        )

        self.assertFalse(is_likely_job_email(email))

    def test_rejects_paid_survey_opportunities(self) -> None:
        email = SourceEmail(
            source_id="survey",
            subject="Re-Paid Survey Opportunity: AI/ML Professionals",
            sender="Research Panel <research@example.com>",
            received_at=None,
            body="Complete a survey for USD 40.",
        )

        self.assertFalse(is_likely_job_email(email))

    def test_rejects_application_status_messages(self) -> None:
        email = SourceEmail(
            source_id="application_status",
            subject="Kiana, thank you for your application!",
            sender="Company Careers <careers@example.com>",
            received_at=None,
            body="Thank you for applying. We received your application.",
        )

        self.assertFalse(is_likely_job_email(email))

    def test_rejects_interview_notifications(self) -> None:
        email = SourceEmail(
            source_id="interview",
            subject="Notification: Virtual Interview: Kiana Haddadi",
            sender="Calendar <calendar@example.com>",
            received_at=None,
            body="Virtual interview invitation details.",
        )

        self.assertFalse(is_likely_job_email(email))

    def test_accepts_known_recruiter_from_env(self) -> None:
        email = SourceEmail(
            source_id="akshay",
            subject="Following up",
            sender="Akshay <akshay@example.com>",
            received_at=None,
            body="Are you open to chatting?",
        )

        with patch.dict("os.environ", {"KNOWN_RECRUITER_NAMES": "akshay"}, clear=False):
            self.assertTrue(is_likely_job_email(email))


if __name__ == "__main__":
    unittest.main()
