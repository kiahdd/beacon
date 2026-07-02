from __future__ import annotations

from pathlib import Path

from .models import SourceEmail


DEFAULT_EMAIL_FIXTURE_DIR = Path("samples/emails")


def load_email_fixtures(directory: Path = DEFAULT_EMAIL_FIXTURE_DIR) -> list[SourceEmail]:
    """Load local `.txt` email fixtures for the POC pipeline.

    Local fixtures let us build and test parsing/scoring without Gmail OAuth or
    real mailbox access. The Gmail integration can later produce the same
    `SourceEmail` objects and reuse the downstream pipeline.
    """

    # A missing fixture directory should behave like an empty inbox, not an app
    # crash. This keeps first-run local development gentle.
    if not directory.exists():
        return []

    emails: list[SourceEmail] = []
    # Sorting makes pipeline output deterministic, which helps tests and makes
    # CLI output easier to compare between runs.
    for path in sorted(directory.glob("*.txt")):
        emails.append(_load_email_fixture(path))
    return emails


def _load_email_fixture(path: Path) -> SourceEmail:
    """Read one fixture file and split simple headers from body text."""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Fixtures use a lightweight email-like format:
    # From: ...
    # Subject: ...
    #
    # body...
    sender = _read_header(lines, "From") or "Unknown"
    subject = _read_header(lines, "Subject") or path.stem
    body_start = _body_start_index(lines)

    return SourceEmail(
        source_id=path.stem,
        subject=subject,
        sender=sender,
        received_at=None,
        body="\n".join(lines[body_start:]).strip(),
    )


def _read_header(lines: list[str], header_name: str) -> str | None:
    """Return a header value from the top of a fixture file."""

    prefix = f"{header_name}:"
    # Only inspect the top of the file so body text that happens to mention
    # "Subject:" or "From:" is not mistaken for metadata.
    for line in lines[:10]:
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return None


def _body_start_index(lines: list[str]) -> int:
    """Find the first body line after the blank header/body separator."""

    for index, line in enumerate(lines):
        if not line.strip():
            return index + 1
    return 0
