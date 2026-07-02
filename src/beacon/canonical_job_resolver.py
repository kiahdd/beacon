from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from urllib import parse, request
from urllib.parse import urlparse

from .env_loader import load_env_file


LINKEDIN_JOB_DOMAINS = {"linkedin.com", "www.linkedin.com", "ca.linkedin.com"}
REJECTED_JOB_DOMAINS = {
    "linkedin.com",
    "www.linkedin.com",
    "ca.linkedin.com",
    "indeed.com",
    "www.indeed.com",
    "ca.indeed.com",
    "glassdoor.com",
    "www.glassdoor.com",
    "ziprecruiter.com",
    "www.ziprecruiter.com",
    "monster.com",
    "www.monster.com",
    "talent.com",
    "www.talent.com",
    "simplyhired.com",
    "www.simplyhired.com",
    "jooble.org",
    "www.jooble.org",
    "workopolis.com",
    "www.workopolis.com",
}
PREFERRED_ATS_DOMAIN_MARKERS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "workdayjobs.com",
)
CAREER_PATH_MARKERS = ("career", "careers", "job", "jobs", "opening", "openings", "position")


@dataclass(frozen=True)
class CanonicalJobResolution:
    """Decision about whether Beacon should fetch a job URL directly."""

    should_fetch_description: bool
    description_status: str | None = None
    description_source: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class SearchResult:
    """One web-search result used to find canonical job posting URLs."""

    url: str
    title: str = ""
    snippet: str = ""


def resolve_source_job_url(url: str | None) -> CanonicalJobResolution:
    """Return how Beacon should handle a source job URL."""

    if is_linkedin_job_url(url):
        return CanonicalJobResolution(
            should_fetch_description=False,
            description_status="linkedin_blocked",
            description_source="linkedin_alert_only",
            reason="LinkedIn job URLs are blocked from direct description fetching.",
        )

    return CanonicalJobResolution(should_fetch_description=True)


def resolve_canonical_job_url(company: str, title: str, location: str | None = None) -> str | None:
    """Search for a canonical company/ATS job URL from company and title."""

    best_url: str | None = None
    best_score = 0
    seen_urls: set[str] = set()

    for query in _canonical_search_queries(company, title, location):
        for result in _search_web(query):
            if result.url in seen_urls:
                continue
            seen_urls.add(result.url)
            score = _score_canonical_candidate(result, company=company, title=title)
            if score > best_score:
                best_url = result.url
                best_score = score

    return best_url


def is_linkedin_job_url(url: str | None) -> bool:
    """Return whether a URL points at a LinkedIn job host Beacon should not fetch."""

    if not url:
        return False

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold()
    return hostname in LINKEDIN_JOB_DOMAINS


def _canonical_search_queries(company: str, title: str, location: str | None = None) -> tuple[str, ...]:
    """Build web-search queries that favor canonical company and ATS pages."""

    base = f'"{company}" "{title}"'
    location_suffix = f' "{location}"' if location else ""
    return (
        f"{base}{location_suffix} careers",
        f"{base}{location_suffix} greenhouse",
        f"{base}{location_suffix} lever",
        f"{base}{location_suffix} ashby",
        f"{base}{location_suffix} workday",
    )


def _search_web(query: str) -> list[SearchResult]:
    """Search the web through Google Custom Search when configured."""

    load_env_file()
    api_key = os.environ.get("GOOGLE_SEARCH_API_KEY")
    search_engine_id = os.environ.get("GOOGLE_SEARCH_ENGINE_ID")
    if not api_key or not search_engine_id:
        return []

    params = parse.urlencode(
        {
            "key": api_key,
            "cx": search_engine_id,
            "q": query,
            "num": "5",
        }
    )
    api_request = request.Request(
        f"https://www.googleapis.com/customsearch/v1?{params}",
        headers={"Accept": "application/json"},
    )
    with request.urlopen(api_request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))

    return [
        SearchResult(
            url=str(item.get("link", "")),
            title=str(item.get("title", "")),
            snippet=str(item.get("snippet", "")),
        )
        for item in payload.get("items", [])
        if item.get("link")
    ]


def _score_canonical_candidate(result: SearchResult, company: str, title: str) -> int:
    """Score whether a search result looks like the canonical job posting."""

    if _is_rejected_job_url(result.url):
        return 0

    parsed = urlparse(result.url)
    hostname = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    searchable_text = f"{result.title} {result.snippet} {result.url}".casefold()
    company_key = _normalize_match_text(company)
    title_tokens = _meaningful_tokens(title)

    score = 0
    if any(marker in hostname for marker in PREFERRED_ATS_DOMAIN_MARKERS):
        score += 80
    elif company_key and company_key in _normalize_match_text(hostname):
        score += 60
    elif any(marker in path for marker in CAREER_PATH_MARKERS):
        score += 30

    if company_key and company_key in _normalize_match_text(searchable_text):
        score += 15

    matched_title_tokens = sum(1 for token in title_tokens if token in searchable_text)
    if title_tokens:
        score += round(25 * matched_title_tokens / len(title_tokens))

    if not any(marker in path for marker in CAREER_PATH_MARKERS) and not any(
        marker in hostname for marker in PREFERRED_ATS_DOMAIN_MARKERS
    ):
        score -= 20

    return max(0, score)


def _is_rejected_job_url(url: str) -> bool:
    """Reject LinkedIn and third-party job aggregator URLs."""

    hostname = (urlparse(url).hostname or "").casefold()
    return hostname in REJECTED_JOB_DOMAINS or any(
        hostname.endswith(f".{domain}") for domain in REJECTED_JOB_DOMAINS
    )


def _meaningful_tokens(value: str) -> tuple[str, ...]:
    """Return title tokens useful for checking search-result relevance."""

    stop_words = {"and", "or", "the", "a", "an", "to", "for", "of", "in", "on", "with"}
    return tuple(
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2 and token not in stop_words
    )


def _normalize_match_text(value: str) -> str:
    """Normalize text for loose company/title matching."""

    return re.sub(r"[^a-z0-9]+", "", value.casefold())
