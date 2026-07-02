from __future__ import annotations

import unittest
from unittest.mock import patch

from beacon.canonical_job_resolver import (
    SearchResult,
    is_linkedin_job_url,
    resolve_canonical_job_url,
    resolve_source_job_url,
)


class CanonicalJobResolverTests(unittest.TestCase):
    """Tests for deciding whether a source job URL can be fetched directly."""

    def test_detects_linkedin_job_domains(self) -> None:
        urls = (
            "https://linkedin.com/jobs/view/123",
            "https://www.linkedin.com/comm/jobs/view/123?trackingId=abc",
            "https://ca.linkedin.com/jobs/view/123",
        )

        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(is_linkedin_job_url(url))

    def test_does_not_match_non_linkedin_or_lookalike_domains(self) -> None:
        urls = (
            "https://cohere.ai/careers/123",
            "https://greenhouse.io/jobs/123",
            "https://notlinkedin.com/jobs/123",
            "https://jobs.linkedin.example.com/jobs/123",
            None,
        )

        for url in urls:
            with self.subTest(url=url):
                self.assertFalse(is_linkedin_job_url(url))

    def test_linkedin_resolution_marks_alert_only_source(self) -> None:
        resolution = resolve_source_job_url("https://www.linkedin.com/jobs/view/123")

        self.assertFalse(resolution.should_fetch_description)
        self.assertEqual(resolution.description_status, "linkedin_blocked")
        self.assertEqual(resolution.description_source, "linkedin_alert_only")

    def test_non_linkedin_resolution_allows_direct_fetch(self) -> None:
        resolution = resolve_source_job_url("https://cohere.ai/careers/123")

        self.assertTrue(resolution.should_fetch_description)
        self.assertIsNone(resolution.description_status)
        self.assertIsNone(resolution.description_source)

    def test_resolve_canonical_job_url_prefers_ats_over_aggregators(self) -> None:
        results_by_query = {
            '"Instacart" "Senior Data Scientist" careers': [
                SearchResult(
                    url="https://www.linkedin.com/jobs/view/123",
                    title="Senior Data Scientist at Instacart",
                ),
                SearchResult(
                    url="https://instacart.careers/job/senior-data-scientist-shopping-experience",
                    title="Senior Data Scientist - Shopping Experience",
                    snippet="Instacart careers",
                ),
            ],
            '"Instacart" "Senior Data Scientist" greenhouse': [
                SearchResult(
                    url="https://boards.greenhouse.io/instacart/jobs/456",
                    title="Senior Data Scientist, Shopping Experience",
                    snippet="Instacart is hiring a Senior Data Scientist.",
                ),
            ],
        }

        with patch(
            "beacon.canonical_job_resolver._search_web",
            side_effect=lambda query: results_by_query.get(query, []),
        ):
            resolved = resolve_canonical_job_url("Instacart", "Senior Data Scientist")

        self.assertEqual(resolved, "https://boards.greenhouse.io/instacart/jobs/456")

    def test_resolve_canonical_job_url_rejects_third_party_aggregators(self) -> None:
        with patch(
            "beacon.canonical_job_resolver._search_web",
            return_value=[
                SearchResult(
                    url="https://ca.indeed.com/viewjob?jk=123",
                    title="Senior Data Scientist - Instacart",
                ),
                SearchResult(
                    url="https://www.glassdoor.com/job-listing/senior-data-scientist",
                    title="Senior Data Scientist",
                ),
                SearchResult(
                    url="https://www.ziprecruiter.com/jobs/instacart-senior-data-scientist",
                    title="Senior Data Scientist",
                ),
                SearchResult(
                    url="https://www.linkedin.com/jobs/view/123",
                    title="Senior Data Scientist at Instacart",
                ),
            ],
        ):
            resolved = resolve_canonical_job_url("Instacart", "Senior Data Scientist")

        self.assertIsNone(resolved)

    def test_resolve_canonical_job_url_does_not_select_job_board_before_company_careers(self) -> None:
        with patch(
            "beacon.canonical_job_resolver._search_web",
            return_value=[
                SearchResult(
                    url="https://www.indeed.com/viewjob?jk=123",
                    title="Applied AI Engineer - Cohere",
                    snippet="Cohere is hiring.",
                ),
                SearchResult(
                    url="https://cohere.com/careers/applied-ai-engineer",
                    title="Applied AI Engineer",
                    snippet="Cohere careers role for Applied AI Engineer.",
                ),
            ],
        ):
            resolved = resolve_canonical_job_url("Cohere", "Applied AI Engineer")

        self.assertEqual(resolved, "https://cohere.com/careers/applied-ai-engineer")

    def test_resolve_canonical_job_url_includes_location_in_queries(self) -> None:
        queries: list[str] = []

        def fake_search(query: str):
            queries.append(query)
            return []

        with patch("beacon.canonical_job_resolver._search_web", side_effect=fake_search):
            resolve_canonical_job_url("Cohere", "Applied AI Engineer", "Toronto")

        self.assertIn('"Cohere" "Applied AI Engineer" "Toronto" careers', queries)


if __name__ == "__main__":
    unittest.main()
