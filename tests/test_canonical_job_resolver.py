from __future__ import annotations

import os
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from beacon.canonical_job_resolver import (
    BraveSearchProvider,
    GoogleCustomSearchProvider,
    SearchResult,
    SearchProviderCheck,
    SerpApiSearchProvider,
    SerperSearchProvider,
    _brave_results,
    check_search_providers,
    _canonical_search_queries,
    _configured_search_providers,
    _google_results,
    _search_web,
    _serpapi_results,
    _serper_results,
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
                SearchResult(
                    url="https://www.flexjobs.com/gjw/instacart/senior-data-scientist/publicjobs?id=123",
                    title="Senior Data Scientist",
                ),
                SearchResult(
                    url="https://career.io/job/senior-data-scientist-instacart-123",
                    title="Senior Data Scientist",
                ),
                SearchResult(
                    url="https://remoteok.com/remote-jobs/remote-data-scientist-instacart-123",
                    title="Remote Senior Data Scientist",
                ),
                SearchResult(
                    url="https://toronto.ca.expertini.com/job/senior-data-scientist-instacart-123",
                    title="Senior Data Scientist",
                ),
                SearchResult(
                    url="https://builtin.com/job/senior-data-scientist/3359295",
                    title="Senior Data Scientist",
                ),
                SearchResult(
                    url="https://lensa.com/job-v1/instacart/remote/senior-data-scientist/123",
                    title="Senior Data Scientist",
                ),
                SearchResult(
                    url="https://www.careerbuilder.com/jobs-data-scientist?page=4",
                    title="Data Scientist Jobs",
                ),
                SearchResult(
                    url="https://www.metaintro.com/job/senior-data-scientist-123",
                    title="Senior Data Scientist",
                ),
                SearchResult(
                    url="https://himalayas.app/companies/acme/jobs/staff-sw-engineer-machine-learning",
                    title="Staff SWE, Machine Learning",
                    snippet="Acme is hiring.",
                ),
                SearchResult(
                    url="https://www.remote-work.app/job/acme-data-scientist-remote",
                    title="Data Scientist",
                    snippet="Acme is hiring.",
                ),
                SearchResult(
                    url="https://www.tealhq.com/job/senior-data-scientist_123",
                    title="Senior Data Scientist",
                    snippet="Acme is hiring.",
                ),
            ],
        ):
            resolved = resolve_canonical_job_url("Instacart", "Senior Data Scientist")

        self.assertIsNone(resolved)

    def test_resolve_canonical_job_url_rejects_unrelated_career_like_pages(self) -> None:
        with patch(
            "beacon.canonical_job_resolver._search_web",
            return_value=[
                SearchResult(
                    url="https://sisne.org/en/lascon-alumni/",
                    title="LASCON Alumni",
                    snippet="Careers, events, and alumni updates.",
                ),
            ],
        ):
            resolved = resolve_canonical_job_url("Empire Life", "Senior Data Scientist")

        self.assertIsNone(resolved)

    def test_resolve_canonical_job_url_rejects_generic_company_careers_page(self) -> None:
        with patch(
            "beacon.canonical_job_resolver._search_web",
            return_value=[
                SearchResult(
                    url="https://redditinc.com/careers",
                    title="Staff Data Scientist, Marketing - Reddit",
                    snippet="Reddit careers.",
                ),
            ],
        ):
            resolved = resolve_canonical_job_url("Reddit, Inc.", "Staff Data Scientist, Marketing")

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
        self.assertIn('site:jobs.ashbyhq.com "Cohere" "Applied AI Engineer" "Toronto"', queries)

    def test_canonical_search_queries_prioritize_ats_site_searches(self) -> None:
        queries = _canonical_search_queries("Cohere", "Applied AI Engineer")

        self.assertEqual(
            queries[:5],
            (
                'site:boards.greenhouse.io "Cohere" "Applied AI Engineer"',
                'site:jobs.lever.co "Cohere" "Applied AI Engineer"',
                'site:jobs.ashbyhq.com "Cohere" "Applied AI Engineer"',
                'site:myworkdayjobs.com "Cohere" "Applied AI Engineer"',
                'site:workdayjobs.com "Cohere" "Applied AI Engineer"',
            ),
        )
        self.assertIn('"Cohere" "Applied AI Engineer" careers', queries[5:])

    def test_configured_search_providers_prefers_serper_then_serpapi_then_brave_then_google(self) -> None:
        env = {
            "SERPER_API_KEY": "serper-key",
            "SERPAPI_API_KEY": "serpapi-key",
            "BRAVE_SEARCH_API_KEY": "brave-key",
            "GOOGLE_SEARCH_API_KEY": "google-key",
            "GOOGLE_SEARCH_ENGINE_ID": "google-cx",
        }

        with patch.dict(os.environ, env, clear=True):
            providers = _configured_search_providers()

        self.assertIsInstance(providers[0], SerperSearchProvider)
        self.assertIsInstance(providers[1], SerpApiSearchProvider)
        self.assertIsInstance(providers[2], BraveSearchProvider)
        self.assertIsInstance(providers[3], GoogleCustomSearchProvider)

    def test_configured_search_providers_supports_serpapi_without_brave_or_google(self) -> None:
        with patch.dict(os.environ, {"SERPAPI_API_KEY": "serpapi-key"}, clear=True):
            providers = _configured_search_providers()

        self.assertEqual(len(providers), 1)
        self.assertIsInstance(providers[0], SerpApiSearchProvider)

    def test_configured_search_providers_supports_brave_without_google(self) -> None:
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "brave-key"}, clear=True):
            providers = _configured_search_providers()

        self.assertEqual(len(providers), 1)
        self.assertIsInstance(providers[0], BraveSearchProvider)

    def test_configured_search_providers_requires_complete_google_config(self) -> None:
        with patch.dict(os.environ, {"GOOGLE_SEARCH_API_KEY": "google-key"}, clear=True):
            providers = _configured_search_providers()

        self.assertEqual(providers, [])

    def test_serper_results_parse_organic_payload(self) -> None:
        results = _serper_results(
            {
                "organic": [
                    {
                        "link": "https://jobs.ashbyhq.com/acme/123",
                        "title": "Staff Engineer",
                        "snippet": "Acme is hiring.",
                    }
                ]
            }
        )

        self.assertEqual(
            results,
            [
                SearchResult(
                    url="https://jobs.ashbyhq.com/acme/123",
                    title="Staff Engineer",
                    snippet="Acme is hiring.",
                )
            ],
        )

    def test_serpapi_results_parse_organic_payload(self) -> None:
        results = _serpapi_results(
            {
                "organic_results": [
                    {
                        "link": "https://jobs.ashbyhq.com/acme/123",
                        "title": "Staff Engineer",
                        "snippet": "Acme is hiring.",
                    }
                ]
            }
        )

        self.assertEqual(
            results,
            [
                SearchResult(
                    url="https://jobs.ashbyhq.com/acme/123",
                    title="Staff Engineer",
                    snippet="Acme is hiring.",
                )
            ],
        )

    def test_brave_results_parse_web_payload(self) -> None:
        results = _brave_results(
            {
                "web": {
                    "results": [
                        {
                            "url": "https://acme.com/careers/staff-engineer",
                            "title": "Staff Engineer",
                            "description": "Acme careers role.",
                        }
                    ]
                }
            }
        )

        self.assertEqual(
            results,
            [
                SearchResult(
                    url="https://acme.com/careers/staff-engineer",
                    title="Staff Engineer",
                    snippet="Acme careers role.",
                )
            ],
        )

    def test_google_results_parse_legacy_custom_search_payload(self) -> None:
        results = _google_results(
            {
                "items": [
                    {
                        "link": "https://jobs.lever.co/acme/123",
                        "title": "Staff Engineer",
                        "snippet": "Acme is hiring.",
                    }
                ]
            }
        )

        self.assertEqual(
            results,
            [
                SearchResult(
                    url="https://jobs.lever.co/acme/123",
                    title="Staff Engineer",
                    snippet="Acme is hiring.",
                )
            ],
        )

    def test_search_web_uses_first_provider_with_results(self) -> None:
        providers = [
            _FakeSearchProvider([]),
            _FakeSearchProvider([SearchResult(url="https://acme.com/careers/123")]),
            _FakeSearchProvider([SearchResult(url="https://jobs.lever.co/acme/456")]),
        ]

        with patch("beacon.canonical_job_resolver.load_env_file"), patch(
            "beacon.canonical_job_resolver._configured_search_providers",
            return_value=providers,
        ):
            results = _search_web('"Acme" "Staff Engineer" careers')

        self.assertEqual(results, [SearchResult(url="https://acme.com/careers/123")])
        self.assertEqual(providers[0].queries, ['"Acme" "Staff Engineer" careers'])
        self.assertEqual(providers[1].queries, ['"Acme" "Staff Engineer" careers'])
        self.assertEqual(providers[2].queries, [])

    def test_search_web_falls_back_when_provider_fails(self) -> None:
        providers = [
            _FailingSearchProvider(),
            _FakeSearchProvider([SearchResult(url="https://acme.com/careers/123")]),
        ]

        with patch("beacon.canonical_job_resolver.load_env_file"), patch(
            "beacon.canonical_job_resolver._configured_search_providers",
            return_value=providers,
        ):
            results = _search_web('"Acme" "Staff Engineer" careers')

        self.assertEqual(results, [SearchResult(url="https://acme.com/careers/123")])

    def test_check_search_providers_reports_success_and_failure(self) -> None:
        providers = [
            _FakeSearchProvider([SearchResult(url="https://acme.com/careers/123")], name="working"),
            _FailingSearchProvider(),
        ]

        with patch("beacon.canonical_job_resolver.load_env_file"), patch(
            "beacon.canonical_job_resolver._configured_search_providers",
            return_value=providers,
        ):
            checks = check_search_providers('"Acme" "Staff Engineer" careers')

        self.assertEqual(
            checks,
            [
                SearchProviderCheck(provider="working", ok=True, result_count=1),
                SearchProviderCheck(
                    provider="failing",
                    ok=False,
                    error="OSError: search provider unavailable",
                ),
            ],
        )

    def test_check_search_providers_reports_no_configured_providers(self) -> None:
        with patch("beacon.canonical_job_resolver.load_env_file"), patch(
            "beacon.canonical_job_resolver._configured_search_providers",
            return_value=[],
        ):
            checks = check_search_providers()

        self.assertEqual(checks, [])


@dataclass
class _FakeSearchProvider:
    results: list[SearchResult]
    name: str = "fake"

    def __post_init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str) -> list[SearchResult]:
        self.queries.append(query)
        return self.results


class _FailingSearchProvider:
    name = "failing"

    def search(self, query: str) -> list[SearchResult]:
        raise OSError("search provider unavailable")


if __name__ == "__main__":
    unittest.main()
