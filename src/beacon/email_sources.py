from __future__ import annotations

import imaplib
import os
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Protocol

from .config import DEFAULT_GMAIL_SETTINGS, GmailImapSettings, GmailSettings
from .email_loader import DEFAULT_EMAIL_FIXTURE_DIR, load_email_fixtures
from .email_filter import is_likely_job_email
from .env_loader import load_env_file
from .models import SourceEmail


class EmailSource(Protocol):
    """Common interface for anything that can provide emails to Beacon."""

    def load_emails(self) -> list[SourceEmail]:
        """Return emails in Beacon's normalized SourceEmail format."""


class LocalFixtureEmailSource:
    """Email source backed by local `.txt` files.

    Gmail can later implement the same `load_emails()` method. The rest of the
    pipeline will not need to know which source produced the SourceEmail list.
    """

    def __init__(self, directory: Path = DEFAULT_EMAIL_FIXTURE_DIR) -> None:
        self.directory = directory

    def load_emails(self) -> list[SourceEmail]:
        """Load emails from the configured fixture directory."""

        return load_email_fixtures(self.directory)


class GmailEmailSource:
    """Future Gmail-backed email source.

    This stub exists so the pipeline already has the right shape. The real
    implementation will authenticate with Gmail, run `settings.search_query`,
    and convert Gmail messages into SourceEmail objects.
    """

    def __init__(self, settings: GmailSettings = DEFAULT_GMAIL_SETTINGS) -> None:
        self.settings = settings

    def load_emails(self) -> list[SourceEmail]:
        """Load emails from Gmail once OAuth/API integration is implemented."""

        raise NotImplementedError(
            "Gmail integration is not configured yet. "
            "Use LocalFixtureEmailSource for the local POC."
        )


class GmailImapEmailSource:
    """Gmail-backed source that uses an app password over IMAP.

    This is the practical local integration for the credentials in `.env`.
    Gmail OAuth/API support can later replace this class while keeping the rest
    of the pipeline unchanged.
    """

    def __init__(
        self,
        settings: GmailImapSettings | None = None,
        env_path: Path = Path(".env"),
        imap_factory: Callable[[str], imaplib.IMAP4_SSL] = imaplib.IMAP4_SSL,
        verbose: bool = True,
    ) -> None:
        self.settings = settings
        self.env_path = env_path
        self.imap_factory = imap_factory
        self.verbose = verbose

    def load_emails(self) -> list[SourceEmail]:
        """Read matching Gmail messages and convert them to SourceEmail."""

        settings = self.settings or self._settings_from_env()
        with self._authenticated_mailbox(settings) as mailbox:
            emails: list[SourceEmail] = []
            seen_source_ids: set[str] = set()
            for mailbox_name in _mailboxes_to_scan(settings):
                mailbox_emails = self._load_mailbox_emails(mailbox, settings, mailbox_name)
                for email in mailbox_emails:
                    if email.source_id in seen_source_ids:
                        continue
                    seen_source_ids.add(email.source_id)
                    emails.append(email)

        return emails

    def list_mailboxes(self) -> list[str]:
        """Return Gmail IMAP mailbox names as Gmail exposes them."""

        settings = self.settings or self._settings_from_env()
        with self._authenticated_mailbox(settings) as mailbox:
            status, data = mailbox.list()
            _ensure_ok(status, "list Gmail mailboxes")
        return [_parse_mailbox_name(item) for item in data if item]

    def _authenticated_mailbox(self, settings: GmailImapSettings) -> imaplib.IMAP4_SSL:
        """Create and authenticate an IMAP connection."""

        if not settings.address or not settings.app_password:
            raise RuntimeError(
                "Gmail IMAP credentials are missing. Add GMAIL_ADDRESS and "
                "GMAIL_APP_PASSWORD to .env."
            )

        mailbox = self.imap_factory(settings.host)
        try:
            mailbox.login(settings.address, settings.app_password)
        except imaplib.IMAP4.error as error:
            raise RuntimeError(
                "Could not log in to Gmail IMAP. Check that GMAIL_ADDRESS "
                "is correct, the app password is current, and Gmail IMAP "
                "access is enabled for the account."
            ) from error
        return mailbox

    def _settings_from_env(self) -> GmailImapSettings:
        """Build IMAP settings from `.env` plus any process overrides."""

        load_env_file(self.env_path)
        app_password = os.environ.get("GMAIL_APP_PASSWORD")
        return GmailImapSettings(
            address=os.environ.get("GMAIL_ADDRESS"),
            app_password=app_password.replace(" ", "") if app_password else None,
            host=os.environ.get("GMAIL_IMAP_HOST", "imap.gmail.com"),
            mailbox=os.environ.get("GMAIL_IMAP_MAILBOX", "INBOX"),
            label_mailboxes=_env_list("GMAIL_IMAP_LABEL_MAILBOXES"),
            search_criteria=os.environ.get("GMAIL_IMAP_SEARCH", "ALL"),
            max_results=int(os.environ.get("GMAIL_IMAP_MAX_RESULTS", "25")),
        )

    def _load_mailbox_emails(
        self,
        mailbox: imaplib.IMAP4_SSL,
        settings: GmailImapSettings,
        mailbox_name: str,
    ) -> list[SourceEmail]:
        """Load candidate emails from one Gmail label/mailbox."""

        status, _ = mailbox.select(_quote_mailbox_name(mailbox_name))
        if status.upper() != "OK":
            self._log(f"Skipping Gmail mailbox {mailbox_name!r}; Gmail returned {status!r}.")
            return []

        status, data = mailbox.search(None, settings.search_criteria)
        _ensure_ok(status, f"search Gmail mailbox {mailbox_name!r}")

        message_ids = data[0].split() if data and data[0] else []
        recent_ids = message_ids[-settings.max_results :]
        self._log(f"Scanning Gmail mailbox {mailbox_name!r}: newest {len(recent_ids)} message(s).")

        emails: list[SourceEmail] = []
        skipped_by_header = 0
        for index, message_id in enumerate(reversed(recent_ids), start=1):
            header_email = _fetch_header_source_email(mailbox, mailbox_name, message_id)
            if header_email is not None and not is_likely_job_email(header_email):
                skipped_by_header += 1
                continue

            status, fetched = mailbox.fetch(message_id, "(RFC822)")
            _ensure_ok(status, f"fetch Gmail message {message_id!r} from {mailbox_name!r}")
            raw_message = _extract_raw_message(fetched)
            if raw_message is None:
                continue
            emails.append(_message_to_source_email(mailbox_name, message_id, raw_message))

            if index % 25 == 0:
                self._log(
                    f"Fetched {index}/{len(recent_ids)} Gmail headers from {mailbox_name!r}; "
                    f"kept {len(emails)} candidates."
                )

        if skipped_by_header:
            self._log(
                f"Skipped {skipped_by_header} Gmail messages by header before full download "
                f"from {mailbox_name!r}."
            )

        return emails

    def _log(self, message: str) -> None:
        """Print Gmail progress in CLI runs while allowing quiet tests."""

        if self.verbose:
            print(message)


def _ensure_ok(status: str, action: str) -> None:
    """Turn IMAP status codes into readable Beacon errors."""

    if status.upper() != "OK":
        raise RuntimeError(f"Could not {action}; Gmail returned {status!r}.")


def _extract_raw_message(fetched: list[object]) -> bytes | None:
    """Find the RFC822 bytes inside an IMAP fetch response."""

    for item in fetched:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _fetch_header_source_email(
    mailbox: imaplib.IMAP4_SSL,
    mailbox_name: str,
    message_id: bytes,
) -> SourceEmail | None:
    """Fetch only cheap headers so obvious noise can be skipped quickly."""

    status, fetched = mailbox.fetch(
        message_id,
        "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])",
    )
    _ensure_ok(status, f"fetch Gmail headers for message {message_id!r}")
    raw_headers = _extract_raw_message(fetched)
    if raw_headers is None:
        return None
    return _message_to_source_email(mailbox_name, message_id, raw_headers)


def _message_to_source_email(mailbox_name: str, message_id: bytes, raw_message: bytes) -> SourceEmail:
    """Convert a Gmail RFC822 message into Beacon's normalized email model."""

    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    received_at = None
    if message["Date"]:
        try:
            received_at = parsedate_to_datetime(str(message["Date"]))
        except (TypeError, ValueError):
            received_at = None

    return SourceEmail(
        source_id=_source_id(mailbox_name, message_id, message),
        subject=str(message["Subject"] or ""),
        sender=str(message["From"] or ""),
        received_at=received_at,
        body=_extract_message_body(message),
    )


def _extract_message_body(message: EmailMessage) -> str:
    """Prefer plain text, falling back to HTML if the email has no text part."""

    if message.is_multipart():
        html_fallback = ""
        for part in message.walk():
            if part.is_multipart():
                continue
            content_type = part.get_content_type()
            content = part.get_content()
            if content_type == "text/plain":
                return str(content)
            if content_type == "text/html" and not html_fallback:
                html_fallback = str(content)
        return html_fallback

    return str(message.get_content())


def _mailboxes_to_scan(settings: GmailImapSettings) -> tuple[str, ...]:
    """Return configured label mailboxes first, then the main mailbox."""

    ordered = list(settings.label_mailboxes) + [settings.mailbox]
    deduped: list[str] = []
    for mailbox_name in ordered:
        if mailbox_name and mailbox_name not in deduped:
            deduped.append(mailbox_name)
    return tuple(deduped)


def _source_id(mailbox_name: str, message_id: bytes, message: EmailMessage) -> str:
    """Prefer RFC Message-ID so duplicate messages across labels collapse."""

    message_id_header = str(message["Message-ID"] or "").strip()
    if message_id_header:
        return f"gmail-imap:{message_id_header}"

    imap_id = message_id.decode(errors="ignore")
    return f"gmail-imap:{mailbox_name}:{imap_id}"


def _env_list(name: str) -> tuple[str, ...]:
    """Parse comma-separated env settings."""

    raw_value = os.environ.get(name, "")
    return tuple(value.strip() for value in raw_value.split(",") if value.strip())


def _parse_mailbox_name(item: bytes) -> str:
    """Extract the mailbox name from an IMAP LIST response line."""

    text = item.decode(errors="replace")
    if ' "/" ' in text:
        return text.rsplit(' "/" ', 1)[1].strip('"')
    return text.rsplit(" ", 1)[-1].strip('"')


def _quote_mailbox_name(mailbox_name: str) -> str:
    """Quote IMAP mailbox names that contain spaces or special characters."""

    escaped = mailbox_name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
