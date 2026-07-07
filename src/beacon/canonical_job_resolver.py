from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Protocol
from urllib import error, parse, request
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
    "flexjobs.com",
    "www.flexjobs.com",
    "career.io",
    "www.career.io",
    "remoteok.com",
    "www.remoteok.com",
    "expertini.com",
    "www.expertini.com",
    "builtin.com",
    "www.builtin.com",
    "lensa.com",
    "www.lensa.com",
    "careerbuilder.com",
    "www.careerbuilder.com",
    "metaintro.com",
    "www.metaintro.com",
    "himalayas.app",
    "www.himalayas.app",
    "remote-work.app",
    "www.remote-work.app",
    "tealhq.com",
    "www.tealhq.com",
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


@dataclass(frozen=True)
class SearchProviderCheck:
    """Connection-check result for one configured search provider."""

    provider: str
    ok: bool
    result_count: int = 0
    error: str | None = None


class SearchProvider(Protocol):
    """Pluggable web search provider used by canonical URL resolution."""

    name: str

    def search(self, query: str) -> list[SearchResult]:
        """Return web search results for a query."""


@dataclass(frozen=True)
class SerperSearchProvider:
    """Serper.dev Google Search API provider."""

    api_key: str
    name: str = "serper"

    def search(self, query: str) -> list[SearchResult]:
        payload = json.dumps({"q": query, "num": 5}).encode("utf-8")
        api_request = request.Request(
            "https://google.serper.dev/search",
            data=payload,
            method="POST",
            headers={
                "X-API-KEY": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with request.urlopen(api_request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        return _serper_results(data)


@dataclass(frozen=True)
class SerpApiSearchProvider:
    """SerpApi Google Search API provider."""

    api_key: str
    name: str = "serpapi"

    def search(self, query: str) -> list[SearchResult]:
        params = parse.urlencode(
            {
                "engine": "google",
                "q": query,
                "api_key": self.api_key,
                "num": "5",
            }
        )
        api_request = request.Request(
            f"https://serpapi.com/search?{params}",
            headers={"Accept": "application/json"},
        )
        with request.urlopen(api_request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        return _serpapi_results(data)


@dataclass(frozen=True)
class BraveSearchProvider:
    """Brave Web Search API provider."""

    api_key: str
    name: str = "brave"

    def search(self, query: str) -> list[SearchResult]:
        params = parse.urlencode({"q": query, "count": "5"})
        api_request = request.Request(
            f"https://api.search.brave.com/res/v1/web/search?{params}",
            headers={
                "X-Subscription-Token": self.api_key,
                "Accept": "application/json",
            },
        )
        with request.urlopen(api_request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        return _brave_results(data)


@dataclass(frozen=True)
class GoogleCustomSearchProvider:
    """Legacy Google Custom Search JSON API provider."""

    api_key: str
    search_engine_id: str
    name: str = "google"

    def search(self, query: str) -> list[SearchResult]:
        params = parse.urlencode(
            {
                "key": self.api_key,
                "cx": self.search_engine_id,
                "q": query,
                "num": "5",
            }
        )
        api_request = request.Request(
            f"https://www.googleapis.com/customsearch/v1?{params}",
            headers={"Accept": "application/json"},
        )
        with request.urlopen(api_request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        return _google_results(data)


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
        f"site:boards.greenhouse.io {base}{location_suffix}",
        f"site:jobs.lever.co {base}{location_suffix}",
        f"site:jobs.ashbyhq.com {base}{location_suffix}",
        f"site:myworkdayjobs.com {base}{location_suffix}",
        f"site:workdayjobs.com {base}{location_suffix}",
        f"{base}{location_suffix} careers",
        f"{base}{location_suffix} greenhouse",
        f"{base}{location_suffix} lever",
        f"{base}{location_suffix} ashby",
        f"{base}{location_suffix} workday",
    )


def _search_web(query: str) -> list[SearchResult]:
    """Search the web through the configured provider chain."""

    load_env_file()
    for provider in _configured_search_providers():
        try:
            results = provider.search(query)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if results:
            return results
    return []


def configured_search_provider_names() -> tuple[str, ...]:
    """Return configured search provider names in the order Beacon will try them."""

    load_env_file()
    return tuple(provider.name for provider in _configured_search_providers())


def check_search_providers(query: str = '"Cohere" "Applied AI Engineer" careers') -> list[SearchProviderCheck]:
    """Run a small live query against configured providers and report health."""

    load_env_file()
    checks: list[SearchProviderCheck] = []
    for provider in _configured_search_providers():
        try:
            results = provider.search(query)
        except error.HTTPError as exc:
            checks.append(
                SearchProviderCheck(
                    provider=provider.name,
                    ok=False,
                    error=f"HTTP {exc.code}: {_read_http_error(exc)}",
                )
            )
        except error.URLError as exc:
            checks.append(
                SearchProviderCheck(
                    provider=provider.name,
                    ok=False,
                    error=f"Network error: {exc.reason}",
                )
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            checks.append(
                SearchProviderCheck(
                    provider=provider.name,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            checks.append(
                SearchProviderCheck(
                    provider=provider.name,
                    ok=True,
                    result_count=len(results),
                )
            )
    return checks


def _configured_search_providers() -> list[SearchProvider]:
    """Return configured search providers in preferred order."""

    providers: list[SearchProvider] = []

    serper_api_key = os.environ.get("SERPER_API_KEY")
    if serper_api_key:
        providers.append(SerperSearchProvider(api_key=serper_api_key))

    serpapi_api_key = os.environ.get("SERPAPI_API_KEY")
    if serpapi_api_key:
        providers.append(SerpApiSearchProvider(api_key=serpapi_api_key))

    brave_api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if brave_api_key:
        providers.append(BraveSearchProvider(api_key=brave_api_key))

    google_api_key = os.environ.get("GOOGLE_SEARCH_API_KEY")
    google_search_engine_id = os.environ.get("GOOGLE_SEARCH_ENGINE_ID")
    if google_api_key and google_search_engine_id:
        providers.append(
            GoogleCustomSearchProvider(
                api_key=google_api_key,
                search_engine_id=google_search_engine_id,
            )
        )

    return providers


def _read_http_error(exc: error.HTTPError) -> str:
    """Return a short provider error message from an HTTPError response."""

    try:
        body = exc.read().decode("utf-8", errors="replace")
    except OSError:
        return exc.reason

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:300].strip()

    provider_error = payload.get("error")
    if isinstance(provider_error, str):
        return provider_error
    if isinstance(provider_error, dict):
        message = provider_error.get("message")
        if isinstance(message, str):
            return message
    return body[:300].strip()


def _serper_results(payload: dict[str, object]) -> list[SearchResult]:
    """Parse Serper organic search results."""

    organic_results = payload.get("organic")
    if not isinstance(organic_results, list):
        return []
    return [
        SearchResult(
            url=str(item.get("link", "")),
            title=str(item.get("title", "")),
            snippet=str(item.get("snippet", "")),
        )
        for item in organic_results
        if isinstance(item, dict) and item.get("link")
    ]


def _serpapi_results(payload: dict[str, object]) -> list[SearchResult]:
    """Parse SerpApi organic search results."""

    organic_results = payload.get("organic_results")
    if not isinstance(organic_results, list):
        return []
    return [
        SearchResult(
            url=str(item.get("link", "")),
            title=str(item.get("title", "")),
            snippet=str(item.get("snippet", "")),
        )
        for item in organic_results
        if isinstance(item, dict) and item.get("link")
    ]


def _brave_results(payload: dict[str, object]) -> list[SearchResult]:
    """Parse Brave web search results."""

    web = payload.get("web")
    if not isinstance(web, dict):
        return []
    results = web.get("results")
    if not isinstance(results, list):
        return []
    return [
        SearchResult(
            url=str(item.get("url", "")),
            title=str(item.get("title", "")),
            snippet=str(item.get("description", "")),
        )
        for item in results
        if isinstance(item, dict) and item.get("url")
    ]


def _google_results(payload: dict[str, object]) -> list[SearchResult]:
    """Parse legacy Google Custom Search results."""

    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [
        SearchResult(
            url=str(item.get("link", "")),
            title=str(item.get("title", "")),
            snippet=str(item.get("snippet", "")),
        )
        for item in items
        if isinstance(item, dict) and item.get("link")
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
    matched_title_tokens = sum(1 for token in title_tokens if token in searchable_text)
    company_matches_text = bool(company_key and company_key in _normalize_match_text(searchable_text))
    company_matches_host = bool(company_key and company_key in _normalize_match_text(hostname))
    is_preferred_ats = any(marker in hostname for marker in PREFERRED_ATS_DOMAIN_MARKERS)
    title_matches_url = any(token in f"{hostname} {path}" for token in title_tokens)

    if not is_preferred_ats and not company_matches_text:
        return 0
    if title_tokens and matched_title_tokens == 0:
        return 0
    if not is_preferred_ats and not company_matches_host:
        return 0
    if not is_preferred_ats and not title_matches_url:
        return 0
    if not is_preferred_ats and _is_generic_careers_page(path):
        return 0

    score = 0
    if is_preferred_ats:
        score += 80
    elif company_matches_host:
        score += 60
    elif any(marker in path for marker in CAREER_PATH_MARKERS):
        score += 30

    if company_matches_text:
        score += 15

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


def _is_generic_careers_page(path: str) -> bool:
    """Return whether a path looks like a broad careers index, not a role page."""

    normalized_path = path.strip("/").casefold()
    return normalized_path in {"career", "careers", "job", "jobs", "opening", "openings", "position", "positions"}


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
