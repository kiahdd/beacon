from __future__ import annotations

import unittest

from beacon.digest import render_digest
from beacon.models import JobOpportunity, ScoredJob


class DigestTests(unittest.TestCase):
    """Tests for rendering ranked job opportunities into action text."""

    def test_digest_sorts_by_score_and_hides_skips_by_default(self) -> None:
        """The digest should show the best actionable jobs first."""
        digest = render_digest(
            [
                _scored("RetailCo", "Junior Data Analyst", 10, "Skip"),
                _scored("Shopify", "Applied AI Engineer", 82, "Apply now"),
                _scored("Cohere", "Applied AI role", 64, "Investigate"),
            ]
        )

        self.assertIn("Beacon ranked action digest", digest)
        self.assertLess(digest.index("Shopify"), digest.index("Cohere"))
        self.assertNotIn("RetailCo", digest)

    def test_digest_includes_location_link_and_explanation(self) -> None:
        """Each digest item should include the fields needed for quick action."""
        digest = render_digest(
            [
                _scored(
                    "Cohere",
                    "Senior Applied AI Engineer",
                    99,
                    "Apply now",
                    location="Remote Canada",
                    link="https://cohere.ai/careers/123456",
                    explanation="Strong AI systems fit.",
                )
            ]
        )

        self.assertIn("Remote Canada", digest)
        self.assertIn("https://cohere.ai/careers/123456", digest)
        self.assertIn("Strong AI systems fit.", digest)

    def test_digest_can_include_skips_when_requested(self) -> None:
        """Debug views can include skipped jobs without changing default behavior."""
        digest = render_digest(
            [_scored("RetailCo", "Junior Data Analyst", 10, "Skip")],
            include_skips=True,
        )

        self.assertIn("RetailCo", digest)
        self.assertIn("Skip", digest)

    def test_digest_handles_no_visible_jobs(self) -> None:
        """If every job is skipped, the normal digest should say so clearly."""
        digest = render_digest([_scored("RetailCo", "Junior Data Analyst", 10, "Skip")])

        self.assertEqual(digest, "No Apply now or Investigate opportunities found.")

    def test_digest_handles_empty_input(self) -> None:
        """An empty pipeline result should not produce a blank digest."""
        digest = render_digest([])

        self.assertEqual(digest, "No job opportunities found yet.")


def _scored(
    company: str,
    title: str,
    score: int,
    category: str,
    location: str | None = "Remote Canada",
    link: str | None = "https://example.com/job",
    explanation: str = "Good fit.",
) -> ScoredJob:
    return ScoredJob(
        job=JobOpportunity(
            company=company,
            title=title,
            location=location,
            job_link=link,
        ),
        score=score,
        category=category,
        explanation=explanation,
    )


if __name__ == "__main__":
    unittest.main()

