from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import patch

from beacon.job_description_fetcher import (
    extract_ats_job_text,
    extract_job_page_text,
    extract_visible_text,
    fetch_job_description,
)


class JobDescriptionFetcherTests(unittest.TestCase):
    """Tests for fetching readable job posting text from public pages."""

    def test_extract_visible_text_removes_script_and_style_content(self) -> None:
        html = """
        <html>
          <head><style>.hidden { color: red; }</style></head>
          <body>
            <h1>Senior ML Engineer</h1>
            <script>console.log("ignore me")</script>
            <p>Build LLM evaluation systems.</p>
          </body>
        </html>
        """

        text = extract_visible_text(html)

        self.assertIn("Senior ML Engineer", text)
        self.assertIn("Build LLM evaluation systems.", text)
        self.assertNotIn("console.log", text)
        self.assertNotIn(".hidden", text)

    def test_fetch_job_description_uses_opener_and_extracts_text(self) -> None:
        long_description = _long_job_description("Applied AI Engineer", "RAG and agents.")
        html = f"<html><body><main>{long_description}</main></body></html>".encode("utf-8")

        result = fetch_job_description("https://example.com/jobs/1", opener=_fake_opener(html))

        self.assertIsNone(result.error)
        self.assertEqual(result.final_url, "https://example.com/jobs/1")
        self.assertIn("Applied AI Engineer", result.description)
        self.assertIn("RAG and agents.", result.description)

    def test_fetch_job_description_prefers_trafilatura_over_visible_text_fallback(self) -> None:
        html = b"""
        <html>
          <body>
            <main>
              <h1>Sign in</h1>
              <p>LinkedIn</p>
              <p>We're signing you in</p>
              <p>Discover people, jobs, and more.</p>
              <p>User Agreement</p>
              <p>Cookie Policy</p>
            </main>
          </body>
        </html>
        """

        before_text = extract_visible_text(html.decode("utf-8"))
        result = fetch_job_description(
            "https://www.linkedin.com/jobs/view/276",
            opener=_fake_opener(html),
            extractor=lambda *_args, **_kwargs: _long_job_description(
                "Senior ML Engineer",
                "Build production RAG evaluation systems.",
            ),
        )

        self.assertIn("We're signing you in", before_text)
        self.assertIsNone(result.error)
        self.assertIn("Senior ML Engineer", result.description)
        self.assertIn("Build production RAG evaluation systems.", result.description)
        self.assertNotIn("We're signing you in", result.description)

    def test_fetch_job_description_rejects_short_extracted_text(self) -> None:
        html = b"<html><body><h1>Applied AI Engineer</h1><p>Build agents.</p></body></html>"

        result = fetch_job_description("https://example.com/jobs/1", opener=_fake_opener(html))

        self.assertIsNone(result.description)
        self.assertEqual(result.error, "description text below 500 characters")

    def test_fetch_job_description_rejects_authwall_phrases(self) -> None:
        text = _long_job_description("Applied AI Engineer", "Login required before viewing this posting.")
        html = f"<html><body><main>{text}</main></body></html>".encode("utf-8")

        result = fetch_job_description(
            "https://example.com/jobs/1",
            opener=_fake_opener(html),
            extractor=lambda *_args, **_kwargs: text,
        )

        self.assertIsNone(result.description)
        self.assertEqual(result.error, "authwall detected")

    def test_fetch_job_description_rejects_join_linkedin_login_wall_html(self) -> None:
        text = _long_job_description("Senior ML Engineer", "Join LinkedIn to view this job.")
        html = f"<html><body><main>{text}</main></body></html>".encode("utf-8")

        result = fetch_job_description(
            "https://example.com/jobs/1",
            opener=_fake_opener(html),
            extractor=lambda *_args, **_kwargs: text,
        )

        self.assertIsNone(result.description)
        self.assertEqual(result.error, "authwall detected")

    def test_extract_job_page_text_uses_ats_parser_before_trafilatura(self) -> None:
        html = """
        <html>
          <body>
            <script type="application/ld+json">
              {
                "@type": "JobPosting",
                "title": "Senior ML Engineer",
                "description": "Responsibilities include building production ML systems.",
                "qualifications": "Requirements include Python and model deployment experience."
              }
            </script>
          </body>
        </html>
        """

        text = extract_job_page_text(
            html,
            extractor=lambda *_args, **_kwargs: self.fail("trafilatura should not run after ATS extraction"),
        )

        self.assertIn("Senior ML Engineer", text)
        self.assertIn("Responsibilities include building production ML systems.", text)
        self.assertIn("Requirements include Python", text)

    def test_extract_ats_job_text_uses_common_job_description_containers(self) -> None:
        html = """
        <html>
          <body>
            <section data-qa="job-description">
              <h1>Applied AI Engineer</h1>
              <p>About the role: build reliable agent workflows.</p>
              <p>Responsibilities include evaluation pipelines.</p>
              <p>Requirements include Python and LLM experience.</p>
            </section>
          </body>
        </html>
        """

        text = extract_ats_job_text(html)

        self.assertIn("Applied AI Engineer", text)
        self.assertIn("Responsibilities include evaluation pipelines.", text)
        self.assertIn("Requirements include Python", text)

    def test_extract_job_page_text_uses_readability_after_trafilatura(self) -> None:
        html = "<html><body><article><p>Readable job content.</p></article></body></html>"

        with patch("beacon.job_description_fetcher.extract_readability_text", return_value="Readability job text"):
            with patch("beacon.job_description_fetcher.extract_selectolax_text") as selectolax_text:
                text = extract_job_page_text(html, extractor=lambda *_args, **_kwargs: None)

        self.assertEqual(text, "Readability job text")
        selectolax_text.assert_not_called()

    def test_extract_job_page_text_uses_selectolax_plain_text_fallback_last(self) -> None:
        html = "<html><body><h1>Fallback ML Engineer</h1><p>Build agents.</p></body></html>"

        with patch("beacon.job_description_fetcher.extract_readability_text", return_value=""):
            text = extract_job_page_text(html, extractor=lambda *_args, **_kwargs: None)

        self.assertIn("Fallback ML Engineer", text)
        self.assertIn("Build agents.", text)

    def test_fetch_job_description_rejects_linkedin_login_wall(self) -> None:
        html = b"""
        <html>
          <body>
            <h1>Sign in</h1>
            <p>LinkedIn</p>
            <p>We're signing you in</p>
            <p>Discover people, jobs, and more.</p>
            <p>If you remain on this page, you'll be signed in.</p>
            <p>User Agreement</p>
            <p>Cookie Policy</p>
            <p>Guest Controls</p>
          </body>
        </html>
        """

        result = fetch_job_description(
            "https://www.linkedin.com/jobs/view/276",
            opener=_fake_opener(html),
            extractor=lambda *_args, **_kwargs: None,
        )

        self.assertIsNone(result.description)
        self.assertEqual(result.error, "authwall detected")

    def test_fetch_job_description_rejects_trafilatura_short_linkedin_login_wall(self) -> None:
        html = b"<html><body>LinkedIn login page</body></html>"

        result = fetch_job_description(
            "https://www.linkedin.com/jobs/view/276",
            opener=_fake_opener(
                html,
                final_url="https://www.linkedin.com/ssr-login/passwordless-email-login",
            ),
            extractor=lambda *_args, **_kwargs: "We're signing you in\nDiscover people, jobs, and more.",
        )

        self.assertIsNone(result.description)
        self.assertEqual(result.error, "login wall detected")

    def test_extract_job_page_text_falls_back_when_trafilatura_finds_nothing(self) -> None:
        html = "<html><body><h1>Applied AI Engineer</h1><p>Build agents.</p></body></html>"

        text = extract_job_page_text(html, extractor=lambda *_args, **_kwargs: None)

        self.assertIn("Applied AI Engineer", text)
        self.assertIn("Build agents.", text)

    def test_trafilatura_empty_result_falls_back_to_selectolax_plain_text(self) -> None:
        html = "<html><body><main><h1>Senior AI Engineer</h1><p>Own evaluation systems.</p></main></body></html>"

        with patch("beacon.job_description_fetcher.extract_readability_text", return_value=""):
            text = extract_job_page_text(html, extractor=lambda *_args, **_kwargs: None)

        self.assertIn("Senior AI Engineer", text)
        self.assertIn("Own evaluation systems.", text)

    def test_fetch_job_description_rejects_unsupported_url_scheme(self) -> None:
        result = fetch_job_description("mailto:jobs@example.com")

        self.assertIsNone(result.description)
        self.assertEqual(result.error, "unsupported URL scheme")


def _fake_opener(payload: bytes, final_url: str | None = None):
    def open_url(api_request, timeout: int):
        return _FakeResponse(payload, final_url or api_request.full_url)

    return open_url


class _FakeResponse:
    def __init__(self, payload: bytes, url: str) -> None:
        self._payload = payload
        self._url = url
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._payload

    def geturl(self) -> str:
        return self._url


def _long_job_description(title: str, sentence: str) -> str:
    return "\n".join(
        (
            title,
            sentence,
            "Responsibilities include building reliable production systems, collaborating with product and engineering teams, designing evaluation workflows, improving model quality, and communicating impact clearly.",
            "Requirements include strong Python experience, machine learning depth, data analysis skills, experimentation judgment, and comfort deploying services that support real users.",
            "This role works across research, platform, and applied teams to turn ambiguous business problems into measurable AI systems with clear ownership and high-quality execution.",
            "The team values thoughtful tradeoffs, pragmatic delivery, mentorship, and durable technical design for models, data pipelines, monitoring, and user-facing product experiences.",
        )
    )
