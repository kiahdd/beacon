from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from beacon.models import SourceEmail
from beacon.parser import parse_email


class ParserTests(unittest.TestCase):
    """Tests for turning email text into structured job opportunities."""

    def test_parses_structured_linkedin_alert(self) -> None:
        """Inline labels should produce a complete Shopify job."""
        email = SourceEmail(
            source_id="shopify",
            subject="Applied AI Engineer - Shopify",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=None,
            body=(
                "Company: Shopify\n"
                "Role: Applied AI Engineer\n"
                "Location: Toronto / Remote Canada\n"
                "\n"
                "Responsibilities\n"
                "- Build LLM applications\n"
                "- AI agents\n"
                "- Evaluation pipelines\n"
                "\n"
                "Preferred\n"
                "- Databricks\n"
                "- RAG\n"
                "\n"
                "Apply:\n"
                "https://careers.shopify.com/jobs/99999\n"
                "\n"
                "Posted 18 minutes ago.\n"
            ),
        )

        jobs = parse_email(email)

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.company, "Shopify")
        self.assertEqual(job.title, "Applied AI Engineer")
        self.assertEqual(job.location, "Toronto / Remote Canada")
        self.assertEqual(job.work_mode, "Remote")
        self.assertEqual(job.job_link, "https://careers.shopify.com/jobs/99999")
        self.assertEqual(job.posted_date, "18 minutes ago")
        self.assertIn("LLM", job.required_skills)
        self.assertIn("Databricks", job.preferred_skills)

    def test_parses_block_labeled_greenhouse_alert(self) -> None:
        """Block labels should handle platform emails without colons."""
        email = SourceEmail(
            source_id="clutch",
            subject="Staff Data Scientist - Clutch",
            sender="Greenhouse <no-reply@greenhouse.io>",
            received_at=None,
            body=(
                "Company\n\n"
                "Clutch\n\n"
                "Location\n\n"
                "Toronto\n\n"
                "Salary\n\n"
                "CA$180k-240k\n\n"
                "Responsibilities\n\n"
                "- Forecasting\n"
                "- Experimentation\n"
                "- Recommendation Systems\n\n"
                "Apply\n\n"
                "https://boards.greenhouse.io/clutch/jobs/111111\n"
            ),
        )

        job = parse_email(email)[0]

        self.assertEqual(job.company, "Clutch")
        self.assertEqual(job.title, "Staff Data Scientist")
        self.assertEqual(job.location, "Toronto")
        self.assertEqual(job.salary_range, "CA$180k-240k")
        self.assertEqual(job.seniority, "Staff")
        self.assertIn("Forecasting", job.required_skills)

    def test_parses_recruiter_prose_with_sender_company_fallback(self) -> None:
        """Recruiter emails should infer role and company from prose/domain."""
        email = SourceEmail(
            source_id="dayforce_recruiter",
            subject="Interested in chatting?",
            sender="Sarah Kim <sarah.kim@dayforce.com>",
            received_at=None,
            body=(
                "We're hiring a Senior Machine Learning Engineer on our AI Platform team.\n"
                "The role focuses on:\n"
                "- ML infrastructure\n"
                "- LLM integration\n"
                "- Model deployment\n"
            ),
        )

        job = parse_email(email)[0]

        self.assertEqual(job.company, "Dayforce")
        self.assertEqual(job.title, "Senior Machine Learning Engineer")
        self.assertEqual(job.seniority, "Senior")
        self.assertIsNone(job.job_link)
        self.assertIn("LLM", job.required_skills)

    def test_parses_on_site_poor_match(self) -> None:
        """On-site wording should be preserved for scoring penalties."""
        email = SourceEmail(
            source_id="marketing",
            subject="Data Scientist - Marketing",
            sender="LinkedIn Jobs",
            received_at=None,
            body=(
                "Location\n\n"
                "California\n\n"
                "On-site\n\n"
                "Requirements\n"
                "- Meta Ads\n"
                "- Google Ads\n"
                "- Attribution\n"
            ),
        )

        job = parse_email(email)[0]

        self.assertEqual(job.company, "Unknown")
        self.assertEqual(job.title, "Data Scientist")
        self.assertEqual(job.location, "California")
        self.assertEqual(job.work_mode, "On-site")
        self.assertIn("Meta Ads", job.required_skills)

    def test_parses_contract_opportunity_subject(self) -> None:
        """Recruiter contract subjects should expose company and role."""
        email = SourceEmail(
            source_id="scotiabank_contract",
            subject="Scotiabank Contract Opportunity BNSJP00041078 Data Scientist",
            sender="Recruiter <person@example.com>",
            received_at=None,
            body=(
                "Looking for a Data Scientist for a contract role.\n"
                "Skills: Python, SQL, machine learning, forecasting.\n"
            ),
        )

        job = parse_email(email)[0]

        self.assertEqual(job.company, "Scotiabank")
        self.assertEqual(job.title, "Data Scientist")
        self.assertIn("Python", job.required_skills)
        self.assertIn("Forecasting", job.required_skills)

    def test_strips_message_replied_prefix(self) -> None:
        """Reply notification prefixes should not become part of the role title."""
        email = SourceEmail(
            source_id="reply",
            subject="Message replied: Exciting machine learning engineer opportunity",
            sender="Recruiter <person@example.com>",
            received_at=None,
            body="The opportunity focuses on ML platform work and Python.",
        )

        job = parse_email(email)[0]

        self.assertEqual(job.title, "Machine Learning Engineer")

    def test_strips_expiring_job_subject_prefix(self) -> None:
        """LinkedIn expiration reminders should expose the underlying role."""
        now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
        email = SourceEmail(
            source_id="expiring",
            subject="Kiana, your job’s expiring on Jul 2: Senior Data Scientist - Shopping Experience (Search)",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=None,
            body="Company: Instacart\nSkills: machine learning, experimentation.",
        )

        job = parse_email(email, now=now)[0]

        self.assertEqual(job.company, "Instacart")
        self.assertEqual(job.title, "Senior Data Scientist")
        self.assertFalse(job.is_expired)

    def test_detects_expired_job_from_closed_wording(self) -> None:
        """Explicit closed/expired language should mark the job as expired."""
        email = SourceEmail(
            source_id="expired",
            subject="Senior Applied AI Engineer",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=None,
            body="This job is no longer accepting applications.\nCompany: Cohere",
        )

        job = parse_email(email)[0]

        self.assertTrue(job.is_expired)

    def test_detects_expired_job_from_past_expiry_date(self) -> None:
        """`Expiring on Jul 2` should become expired after that date passes."""
        now = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
        email = SourceEmail(
            source_id="past_expiry",
            subject="Kiana, your job’s expiring on Jul 2: Senior Data Scientist",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=None,
            body="Company: Instacart\nSkills: machine learning.",
        )

        job = parse_email(email, now=now)[0]

        self.assertTrue(job.is_expired)

    def test_strips_new_jobs_similar_to_prefix(self) -> None:
        """LinkedIn digest headings should not become literal job titles."""
        email = SourceEmail(
            source_id="similar",
            subject="New jobs similar to Staff Data Scientist",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=None,
            body="Company: Clio\nSkills: forecasting, experimentation.",
        )

        job = parse_email(email)[0]

        self.assertEqual(job.company, "Clio")
        self.assertEqual(job.title, "Staff Data Scientist")

    def test_parses_linkedin_company_is_hiring_subject(self) -> None:
        """`StackAdapt is hiring...` should expose StackAdapt as the company."""
        email = SourceEmail(
            source_id="stackadapt_hiring",
            subject="StackAdapt is hiring a Senior/Staff Applied Machine Learning Scientist",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=None,
            body="Skills: machine learning, LLM, recommendation systems.",
        )

        job = parse_email(email)[0]

        self.assertEqual(job.company, "StackAdapt")
        self.assertEqual(job.title, "Senior/Staff Applied Machine Learning Scientist")

    def test_parses_linkedin_digest_cards_instead_of_broad_subject(self) -> None:
        """LinkedIn digest card titles should not be misread as companies."""
        email = SourceEmail(
            source_id="linkedin_digest",
            subject="Applied AI Engineer jobs for you",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=None,
            body=(
                "Recommended jobs\n\n"
                "AI / ML Ops Lead / Architect posted on 7/6/26\n"
                "Acme AI\n"
                "Toronto, ON (Remote)\n"
                "View job\n"
                "https://www.linkedin.com/comm/jobs/view/4433244312/?trackingId=abc\n\n"
                "Principal Platform Engineer, ML posted on 7/5/26\n"
                "Doppel\n"
                "Canada Remote\n"
                "https://www.linkedin.com/comm/jobs/view/4436983390/?trackingId=def\n"
            ),
        )

        jobs = parse_email(email)

        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].company, "Acme AI")
        self.assertEqual(jobs[0].title, "AI / ML Ops Lead / Architect")
        self.assertEqual(jobs[0].location, "Toronto, ON (Remote)")
        self.assertEqual(jobs[0].work_mode, "Remote")
        self.assertEqual(jobs[0].posted_date, "posted on 7/6/26")
        self.assertEqual(jobs[0].job_link, "https://www.linkedin.com/comm/jobs/view/4433244312/?trackingId=abc")
        self.assertEqual(jobs[1].company, "Doppel")
        self.assertEqual(jobs[1].title, "Principal Platform Engineer, ML")

    def test_linkedin_digest_skips_cards_without_company(self) -> None:
        """Digest cards with no company should not invent one from posted metadata."""
        email = SourceEmail(
            source_id="linkedin_digest_missing_company",
            subject="Applied AI Engineer jobs for you",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=None,
            body=(
                "Senior MLOps Engineer posted on 7/4/26\n"
                "Remote\n"
                "https://www.linkedin.com/comm/jobs/view/4400299927/?trackingId=abc\n"
            ),
        )

        jobs = parse_email(email)

        self.assertEqual(jobs, [])

    def test_linkedin_digest_does_not_treat_role_line_as_location(self) -> None:
        """Role lines with remote text should not become the location field."""
        email = SourceEmail(
            source_id="linkedin_digest_role_location",
            subject="Data Scientist jobs for you",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=None,
            body=(
                "Data Scientist posted on 7/7/26\n"
                "Wealthsimple\n"
                "Senior Machine Learning Engineer (Remote, Canada)\n"
                "Canada\n"
                "https://www.linkedin.com/comm/jobs/view/4436764107/?trackingId=abc\n"
            ),
        )

        jobs = parse_email(email)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].company, "Wealthsimple")
        self.assertEqual(jobs[0].title, "Data Scientist")
        self.assertEqual(jobs[0].location, "Canada")

    def test_normalizes_company_and_title_display_names(self) -> None:
        """Parsed jobs should use canonical company and title formatting."""
        email = SourceEmail(
            source_id="normalization",
            subject="kiana, apply to senior ai engineer (remote) - ada cx: up to date",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=None,
            body="Skills: LLM, RAG, evaluation.",
        )

        job = parse_email(email)[0]

        self.assertEqual(job.company, "Ada CX")
        self.assertEqual(job.title, "Senior AI Engineer")

    def test_company_is_hiring_subject_wins_over_unrelated_dash_suffix(self) -> None:
        """LinkedIn snippets may include unrelated text after a dash."""
        email = SourceEmail(
            source_id="stackadapt_suffix",
            subject="StackAdapt is hiring a Applied Machine Learning Scientist (Remote) - Wealthsimple",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=None,
            body="Skills: machine learning, experimentation.",
        )

        job = parse_email(email)[0]

        self.assertEqual(job.company, "StackAdapt")
        self.assertEqual(job.title, "Applied Machine Learning Scientist")

    def test_cleans_company_names_with_alert_suffixes(self) -> None:
        """Company strings like `Dayforce: update` should keep only the name."""
        email = SourceEmail(
            source_id="company_suffix",
            subject="Senior Machine Learning Engineer at Dayforce: update from LinkedIn",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=None,
            body="Skills: LLM, ML platform.",
        )

        job = parse_email(email)[0]

        self.assertEqual(job.company, "Dayforce")

    def test_parses_scotiabank_delpath_recruiter_body(self) -> None:
        """Known recruiter prose should still become a structured job."""
        email = SourceEmail(
            source_id="akshay_scotiabank",
            subject="Following up",
            sender="Akshay <akshay@example.com>",
            received_at=None,
            body=(
                "It's a 12-month Scotiabank contract via Delpath.\n\n"
                "Role: Data Scientist - 3\n"
                "Business line: Global AI & ML\n"
                "Location: Hybrid, 2 days/week, 44 King St W, Toronto\n"
                "Focus: LLM-powered applied AI solutions\n"
                "Work: prompt engineering, LLM evaluation, RAG/embeddings, "
                "guardrails, Python pipelines, production monitoring\n"
                "FTE possible: yes, but not guaranteed\n"
            ),
        )

        job = parse_email(email)[0]

        self.assertEqual(job.company, "Scotiabank")
        self.assertEqual(job.title, "Data Scientist")
        self.assertEqual(job.location, "Hybrid, 2 days/week, 44 King St W, Toronto")
        self.assertEqual(job.work_mode, "Hybrid")
        self.assertEqual(job.seniority, "Senior")
        self.assertIn("LLM", job.required_skills)
        self.assertIn("RAG", job.required_skills)
        self.assertIn("Python", job.required_skills)

    def test_uses_email_date_when_posted_age_is_missing(self) -> None:
        """Email received_at gives Beacon a fallback posting age."""
        now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
        email = SourceEmail(
            source_id="fresh",
            subject="Senior Data Scientist at Empire Life",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=now - timedelta(days=2, hours=1),
            body="Company: Empire Life\nSkills: machine learning, MLOps.",
        )

        job = parse_email(email, now=now)[0]

        self.assertEqual(job.posted_date, "2 days ago")

    def test_recalculates_relative_posted_text_from_email_date(self) -> None:
        """`Posted 37 minutes ago` should not stay stale across later runs."""
        email_time = datetime(2026, 6, 29, 19, 53, tzinfo=UTC)
        now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
        email = SourceEmail(
            source_id="dayforce",
            subject="Senior Machine Learning Engineer at Dayforce",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=email_time,
            body=(
                "Company: Dayforce\n"
                "Role: Senior Machine Learning Engineer\n"
                "Posted 37 minutes ago.\n"
            ),
        )

        job = parse_email(email, now=now)[0]

        self.assertEqual(job.posted_date, "2 days ago")

    def test_preserves_recent_posted_age_precision(self) -> None:
        """Very recent job alerts should keep minute precision for highlighting."""
        email_time = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
        now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
        email = SourceEmail(
            source_id="fresh_linkedin",
            subject="Senior Applied AI Engineer at Cohere",
            sender="LinkedIn Jobs <jobs-noreply@linkedin.com>",
            received_at=email_time,
            body=(
                "Company: Cohere\n"
                "Role: Senior Applied AI Engineer\n"
                "Posted 37 minutes ago.\n"
            ),
        )

        job = parse_email(email, now=now)[0]

        self.assertEqual(job.posted_date, "37 minutes ago")


if __name__ == "__main__":
    unittest.main()
