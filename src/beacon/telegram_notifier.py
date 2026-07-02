from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib import error, parse, request

from .env_loader import load_env_file


TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_MESSAGE_LIMIT = 4096
SAFE_MESSAGE_LIMIT = 3800


@dataclass(frozen=True)
class TelegramSettings:
    """Settings needed to send a Telegram message through a bot."""

    bot_token: str
    chat_id: str


def load_telegram_settings(env_path: Path = Path(".env")) -> TelegramSettings:
    """Load Telegram bot settings from environment variables or `.env`."""

    load_env_file(env_path)
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", bot_token),
            ("TELEGRAM_CHAT_ID", chat_id),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing Telegram setting(s): {', '.join(missing)}")
    return TelegramSettings(bot_token=bot_token, chat_id=chat_id)


def send_telegram_message(
    text: str,
    settings: TelegramSettings | None = None,
) -> None:
    """Send plain-text Telegram message text, splitting if needed."""

    active_settings = settings or load_telegram_settings()
    for chunk in _split_telegram_text(text):
        _send_telegram_chunk(chunk, active_settings)


def fetch_telegram_updates(
    settings: TelegramSettings | None = None,
    offset: int | None = None,
    limit: int = 20,
    timeout: int = 0,
) -> list[dict[str, object]]:
    """Fetch pending Telegram updates for the configured bot."""

    active_settings = settings or load_telegram_settings()
    query = {
        "limit": str(limit),
        "timeout": str(timeout),
    }
    if offset is not None:
        query["offset"] = str(offset)

    url = (
        f"{TELEGRAM_API_BASE}/bot{active_settings.bot_token}/getUpdates?"
        f"{parse.urlencode(query)}"
    )
    api_request = request.Request(url, method="GET")
    try:
        with request.urlopen(api_request, timeout=timeout + 20) as response:
            response_body = response.read().decode("utf-8")
    except error.HTTPError as http_error:
        response_body = http_error.read().decode("utf-8", errors="replace")
        raise RuntimeError(_telegram_error_message(response_body, http_error.code)) from http_error

    parsed = json.loads(response_body)
    if not parsed.get("ok"):
        description = parsed.get("description", "unknown Telegram API error")
        raise RuntimeError(f"Telegram update fetch failed: {description}")
    return list(parsed.get("result", []))


def _send_telegram_chunk(text: str, settings: TelegramSettings) -> None:
    """Send one Telegram message chunk."""

    payload = parse.urlencode(
        {
            "chat_id": settings.chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    url = f"{TELEGRAM_API_BASE}/bot{settings.bot_token}/sendMessage"
    api_request = request.Request(url, data=payload, method="POST")
    try:
        with request.urlopen(api_request, timeout=20) as response:
            response_body = response.read().decode("utf-8")
    except error.HTTPError as http_error:
        response_body = http_error.read().decode("utf-8", errors="replace")
        raise RuntimeError(_telegram_error_message(response_body, http_error.code)) from http_error

    parsed = json.loads(response_body)
    if not parsed.get("ok"):
        description = parsed.get("description", "unknown Telegram API error")
        raise RuntimeError(f"Telegram send failed: {description}")


def _split_telegram_text(text: str) -> list[str]:
    """Split long text into chunks under Telegram's message-size limit."""

    if len(text) <= TELEGRAM_MESSAGE_LIMIT:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.splitlines(keepends=True):
        if len(paragraph) > SAFE_MESSAGE_LIMIT:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.extend(_split_long_line(paragraph))
            continue

        if len(current) + len(paragraph) > SAFE_MESSAGE_LIMIT:
            chunks.append(current.rstrip())
            current = paragraph
        else:
            current += paragraph

    if current:
        chunks.append(current.rstrip())
    return chunks


def _split_long_line(line: str) -> list[str]:
    """Split a single long line into safe Telegram chunks."""

    return [
        line[index : index + SAFE_MESSAGE_LIMIT].rstrip()
        for index in range(0, len(line), SAFE_MESSAGE_LIMIT)
    ]


def _telegram_error_message(response_body: str, status_code: int) -> str:
    """Extract Telegram's API error description from an HTTP error body."""

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError:
        return f"Telegram send failed with HTTP {status_code}: {response_body}"

    description = parsed.get("description")
    if description:
        return f"Telegram send failed: {description}"
    return f"Telegram send failed with HTTP {status_code}"
