from __future__ import annotations

import unittest

from beacon.dedupe import dedupe_jobs, job_identity_key
from beacon.models import JobOpportunity


class DedupeTests(unittest.TestCase):
    """Tests for collapsing repeated job opportunities before scoring."""

    def test_identity_key_normalizes_case_and_whitespace(self) -> None:
        """Small formatting differences should not create different keys."""
        job = JobOpportunity(
            company="  Dayforce ",
            title="Senior Machine Learning Engineer",
            location="Remote   Canada",
            job_link="HTTPS://JOBS.DAYFORCE.COM/12345",
        )

        key = job_identity_key(job)

        self.assertEqual(
            key,
            (
                "dayforce",
                "senior machine learning engineer",
                "remote canada",
                "https://jobs.dayforce.com/12345",
            ),
        )

    def test_dedupes_jobs_and_preserves_first_copy(self) -> None:
        """The first version is kept so source ordering stays meaningful."""
        first = JobOpportunity(
            company="Dayforce",
            title="Senior Machine Learning Engineer",
            location="Remote Canada",
            job_link="https://jobs.dayforce.com/12345",
            salary_range="CA$145,000-210,000",
        )
        duplicate = JobOpportunity(
            company="dayforce",
            title="Senior Machine Learning Engineer",
            location="Remote Canada",
            job_link="https://jobs.dayforce.com/12345",
            salary_range=None,
        )
        other = JobOpportunity(
            company="Shopify",
            title="Applied AI Engineer",
            location="Toronto / Remote Canada",
            job_link="https://careers.shopify.com/jobs/99999",
        )

        deduped, duplicate_count = dedupe_jobs([first, duplicate, other])

        self.assertEqual(duplicate_count, 1)
        self.assertEqual(deduped, [first, other])

    def test_missing_optional_fields_can_still_dedupe(self) -> None:
        """Recruiter-style jobs without links can still match on known fields."""
        first = JobOpportunity(
            company="Cohere",
            title="Applied AI role",
            location=None,
            job_link=None,
        )
        duplicate = JobOpportunity(
            company=" cohere ",
            title="Applied AI role",
            location=None,
            job_link=None,
        )

        deduped, duplicate_count = dedupe_jobs([first, duplicate])

        self.assertEqual(duplicate_count, 1)
        self.assertEqual(deduped, [first])


if __name__ == "__main__":
    unittest.main()

