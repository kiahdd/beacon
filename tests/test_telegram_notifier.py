from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib import parse
from urllib.error import HTTPError
from unittest.mock import patch

from beacon.telegram_notifier import (
    SAFE_MESSAGE_LIMIT,
    TELEGRAM_MESSAGE_LIMIT,
    TelegramSettings,
    fetch_telegram_updates,
    load_telegram_settings,
    send_telegram_message,
)


class TelegramNotifierTests(unittest.TestCase):
    """Tests for Telegram settings and message sending."""

    def test_loads_telegram_settings_from_env_file(self) -> None:
        """Telegram credentials should load from `.env` without printing secrets."""
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "TELEGRAM_BOT_TOKEN=test-token\nTELEGRAM_CHAT_ID=12345\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = load_telegram_settings(env_path)

        self.assertEqual(settings.bot_token, "test-token")
        self.assertEqual(settings.chat_id, "12345")

    def test_missing_telegram_settings_raise_clear_error(self) -> None:
        """A missing token or chat id should fail before network calls."""
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "TELEGRAM_BOT_TOKEN"):
                    load_telegram_settings(env_path)

    def test_send_telegram_message_posts_to_bot_api(self) -> None:
        """Sending should call Telegram's sendMessage endpoint with form data."""
        fake_response = _FakeResponse({"ok": True})

        with patch("beacon.telegram_notifier.request.urlopen", return_value=fake_response) as urlopen:
            send_telegram_message(
                "Beacon test",
                settings=TelegramSettings(bot_token="token-123", chat_id="chat-456"),
            )

        api_request = urlopen.call_args.args[0]
        payload = api_request.data.decode("utf-8")
        self.assertIn("/bottoken-123/sendMessage", api_request.full_url)
        self.assertIn("chat_id=chat-456", payload)
        self.assertIn("text=Beacon+test", payload)

    def test_fetch_telegram_updates_reads_results(self) -> None:
        """Polling should return Telegram update payloads."""
        fake_response = _FakeResponse(
            {
                "ok": True,
                "result": [{"update_id": 10, "message": {"text": "/applied 1"}}],
            }
        )

        with patch("beacon.telegram_notifier.request.urlopen", return_value=fake_response) as urlopen:
            updates = fetch_telegram_updates(
                settings=TelegramSettings(bot_token="token-123", chat_id="chat-456"),
                offset=7,
                limit=2,
            )

        self.assertEqual(updates[0]["update_id"], 10)
        self.assertIn("offset=7", urlopen.call_args.args[0].full_url)
        self.assertIn("limit=2", urlopen.call_args.args[0].full_url)

    def test_send_telegram_message_splits_long_text(self) -> None:
        """Long messages should be split before hitting Telegram's API limit."""
        fake_response = _FakeResponse({"ok": True})
        long_text = ("job line\n" * 700).strip()

        with patch("beacon.telegram_notifier.request.urlopen", return_value=fake_response) as urlopen:
            send_telegram_message(
                long_text,
                settings=TelegramSettings(bot_token="token-123", chat_id="chat-456"),
            )

        self.assertGreater(urlopen.call_count, 1)
        for call_args in urlopen.call_args_list:
            api_request = call_args.args[0]
            payload = parse.parse_qs(api_request.data.decode("utf-8"))
            message_text = payload["text"][0]
            self.assertLessEqual(len(message_text), SAFE_MESSAGE_LIMIT)

    def test_send_telegram_message_splits_single_very_long_line(self) -> None:
        """A single huge line should not bypass message splitting."""
        fake_response = _FakeResponse({"ok": True})
        long_text = "x" * (TELEGRAM_MESSAGE_LIMIT + 200)

        with patch("beacon.telegram_notifier.request.urlopen", return_value=fake_response) as urlopen:
            send_telegram_message(
                long_text,
                settings=TelegramSettings(bot_token="token-123", chat_id="chat-456"),
            )

        self.assertEqual(urlopen.call_count, 2)

    def test_send_telegram_message_raises_on_api_error(self) -> None:
        """Telegram API errors should become RuntimeError messages."""
        fake_response = _FakeResponse({"ok": False, "description": "chat not found"})

        with patch("beacon.telegram_notifier.request.urlopen", return_value=fake_response):
            with self.assertRaisesRegex(RuntimeError, "chat not found"):
                send_telegram_message(
                    "Beacon test",
                    settings=TelegramSettings(bot_token="token-123", chat_id="bad-chat"),
                )

    def test_send_telegram_message_reads_http_error_body(self) -> None:
        """HTTP 400 responses should show Telegram's real error description."""
        http_error = HTTPError(
            url="https://api.telegram.org/bottoken/sendMessage",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=_FakeResponse({"ok": False, "description": "Bad Request: chat not found"}),
        )

        with patch("beacon.telegram_notifier.request.urlopen", side_effect=http_error):
            with self.assertRaisesRegex(RuntimeError, "chat not found"):
                send_telegram_message(
                    "Beacon test",
                    settings=TelegramSettings(bot_token="token-123", chat_id="bad-chat"),
                )


class _FakeResponse:
    """Tiny context-manager response object for urlopen tests."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
