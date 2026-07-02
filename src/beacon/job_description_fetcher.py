from __future__ import annotations

import re
import json
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib import error, request
from urllib.parse import urlparse

import trafilatura
from readability import Document
from selectolax.parser import HTMLParser as SelectolaxHTMLParser


MAX_DESCRIPTION_CHARS = 20_000
MIN_DESCRIPTION_CHARS = 500
AUTHWALL_PHRASES = (
    "sign in",
    "join linkedin",
    "login",
    "authwall",
)


@dataclass(frozen=True)
class JobDescriptionFetchResult:
    """Result of trying to enrich a job from its public posting URL."""

    description: str | None
    final_url: str | None
    error: str | None = None


def fetch_job_description(
    url: str | None,
    timeout: int = 20,
    opener=None,
    extractor=None,
) -> JobDescriptionFetchResult:
    """Fetch a job page and return readable text for later AI analysis."""

    if not url:
        return JobDescriptionFetchResult(description=None, final_url=None, error="missing job URL")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return JobDescriptionFetchResult(description=None, final_url=url, error="unsupported URL scheme")

    api_request = request.Request(
        url,
        headers={
            "User-Agent": "BeaconCareerCopilot/0.1 (+local job description enrichment)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7",
        },
    )
    open_url = opener or request.urlopen

    try:
        with open_url(api_request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            payload = response.read()
            final_url = response.geturl()
    except error.HTTPError as http_error:
        return JobDescriptionFetchResult(description=None, final_url=url, error=f"HTTP {http_error.code}")
    except error.URLError as url_error:
        return JobDescriptionFetchResult(description=None, final_url=url, error=str(url_error.reason))
    except OSError as os_error:
        return JobDescriptionFetchResult(description=None, final_url=url, error=str(os_error))

    text = _decode_payload(payload, content_type)
    if "html" in content_type.casefold() or _looks_like_html(text):
        text = extract_job_page_text(text, url=final_url, extractor=extractor)
    else:
        text = _normalize_text(text)

    if not text:
        return JobDescriptionFetchResult(description=None, final_url=final_url, error="no readable text found")
    quality_error = _description_quality_error(text, final_url)
    if quality_error:
        return JobDescriptionFetchResult(description=None, final_url=final_url, error=quality_error)

    return JobDescriptionFetchResult(
        description=text[:MAX_DESCRIPTION_CHARS],
        final_url=final_url,
        error=None,
    )


def _description_quality_error(text: str, url: str | None = None) -> str | None:
    """Return why extracted description text is not usable enough to store."""

    if _looks_like_authwall_text(text):
        return "authwall detected"
    if _looks_like_login_wall(text, url):
        return "login wall detected"
    if len(text) < MIN_DESCRIPTION_CHARS:
        return "description text below 500 characters"
    return None


def _looks_like_authwall_text(text: str) -> bool:
    """Return whether extracted text contains generic auth-wall phrases."""

    normalized = text.casefold()
    return any(phrase in normalized for phrase in AUTHWALL_PHRASES)


def extract_job_page_text(html: str, url: str | None = None, extractor=None) -> str:
    """Extract job-page text through ATS, trafilatura, readability, then plain text."""

    ats_text = extract_ats_job_text(html)
    if ats_text:
        return ats_text

    extract = extractor or trafilatura.extract
    extracted = extract(
        html,
        url=url,
        favor_precision=True,
        include_comments=False,
        include_tables=False,
    )
    text = _normalize_text(extracted or "")
    if text:
        return text

    readability_text = extract_readability_text(html)
    if readability_text:
        return readability_text

    return extract_selectolax_text(html)


def extract_ats_job_text(html: str) -> str:
    """Extract content from common ATS job-page structures."""

    structured_text = _extract_structured_job_posting_text(html)
    if structured_text:
        return structured_text

    tree = SelectolaxHTMLParser(html)
    selectors = (
        "[data-qa='job-description']",
        "[data-testid='job-description']",
        "[data-test='job-description']",
        ".job-description",
        "#job-description",
        ".posting-description",
        ".section-wrapper",
        ".content-intro",
        ".ashby-job-posting",
        ".job__description",
        ".description",
        "main",
        "article",
    )
    for selector in selectors:
        node = tree.css_first(selector)
        if node is None:
            continue
        text = _normalize_text(node.text(separator="\n"))
        if _looks_like_job_description(text):
            return text
    return ""


def extract_readability_text(html: str) -> str:
    """Extract readable content with python-readability."""

    try:
        summary_html = Document(html).summary(html_partial=True)
    except Exception:
        return ""
    return extract_selectolax_text(summary_html)


def extract_selectolax_text(html: str) -> str:
    """Extract plain visible text with selectolax as the final fallback."""

    tree = SelectolaxHTMLParser(html)
    for node in tree.css("script, style, noscript, svg, canvas"):
        node.decompose()
    return _normalize_text(tree.body.text(separator="\n") if tree.body else tree.text(separator="\n"))


def extract_visible_text(html: str) -> str:
    """Extract readable page text with Beacon's lightweight fallback parser."""

    parser = _VisibleTextParser()
    parser.feed(html)
    parser.close()
    return _normalize_text("\n".join(parser.parts))


def _extract_structured_job_posting_text(html: str) -> str:
    """Extract JSON-LD JobPosting fields when ATS pages expose them."""

    tree = SelectolaxHTMLParser(html)
    chunks: list[str] = []
    for node in tree.css("script[type='application/ld+json']"):
        raw_text = node.text()
        if not raw_text:
            continue
        for item in _json_objects(raw_text):
            job_text = _job_posting_text_from_json(item)
            if job_text:
                chunks.append(job_text)
    return _normalize_text("\n".join(chunks))


def _json_objects(raw_text: str) -> list[object]:
    """Return JSON objects from one JSON-LD script tag."""

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        graph = parsed.get("@graph")
        if isinstance(graph, list):
            return graph
        return [parsed]
    return []


def _job_posting_text_from_json(item: object) -> str:
    """Return useful text from a JSON-LD JobPosting object."""

    if not isinstance(item, dict):
        return ""
    item_type = item.get("@type")
    if isinstance(item_type, list):
        is_job_posting = any(str(value).casefold() == "jobposting" for value in item_type)
    else:
        is_job_posting = str(item_type).casefold() == "jobposting"
    if not is_job_posting:
        return ""

    parts = [
        str(item.get("title") or ""),
        str(item.get("description") or ""),
        str(item.get("responsibilities") or ""),
        str(item.get("qualifications") or ""),
        str(item.get("skills") or ""),
    ]
    return _normalize_text("\n".join(parts))


def _looks_like_job_description(text: str) -> bool:
    """Return whether extracted text has enough job-posting signal."""

    lower_text = text.casefold()
    if len(text) < 120:
        return False
    markers = (
        "responsibilities",
        "requirements",
        "qualifications",
        "about the role",
        "what you'll do",
        "what you will do",
        "experience",
        "apply",
    )
    return sum(marker in lower_text for marker in markers) >= 2


def _decode_payload(payload: bytes, content_type: str) -> str:
    """Decode bytes from a webpage, respecting simple charset hints."""

    charset_match = re.search(r"charset=([^;\s]+)", content_type, flags=re.IGNORECASE)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _looks_like_html(text: str) -> bool:
    """Detect HTML when a server omits or misstates the content type."""

    sample = text[:1000].casefold()
    return "<html" in sample or "<body" in sample or "<!doctype html" in sample


def _normalize_text(text: str) -> str:
    """Collapse noisy page whitespace while preserving paragraph breaks."""

    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    non_empty = [line for line in lines if line]
    return "\n".join(non_empty).strip()


def _looks_like_login_wall(text: str, url: str | None = None) -> bool:
    """Return whether extracted text is a login/interstitial page, not a job."""

    normalized = " ".join(text.casefold().split())
    normalized_url = (url or "").casefold()
    login_markers = (
        "sign in",
        "we're signing you in",
        "discover people, jobs, and more",
        "if you remain on this page",
        "user agreement",
        "cookie policy",
        "guest controls",
    )
    if "linkedin.com/ssr-login" in normalized_url:
        return True
    if "we're signing you in" in normalized and "discover people, jobs, and more" in normalized:
        return True
    if "linkedin" in normalized and sum(marker in normalized for marker in login_markers) >= 3:
        return True
    return False


class _VisibleTextParser(HTMLParser):
    """Small HTML text extractor for public job pages."""

    _ignored_tags = {"script", "style", "noscript", "svg", "canvas"}
    _block_tags = {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._ignored_tags:
            self._ignored_depth += 1
        if tag in self._block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
        if tag in self._block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = data.strip()
        if text:
            self.parts.append(text)
