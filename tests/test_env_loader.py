from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from beacon.env_loader import load_env_file


class EnvLoaderTests(unittest.TestCase):
    """Tests for Beacon's small .env parser."""

    def test_load_env_file_handles_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("\ufeffSERPAPI_API_KEY=test-key\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                loaded = load_env_file(env_path)

                self.assertEqual(loaded["SERPAPI_API_KEY"], "test-key")
                self.assertEqual(os.environ["SERPAPI_API_KEY"], "test-key")

    def test_load_env_file_keeps_existing_environment_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("SERPAPI_API_KEY=file-key\n", encoding="utf-8")

            with patch.dict(os.environ, {"SERPAPI_API_KEY": "existing-key"}, clear=True):
                loaded = load_env_file(env_path)

                self.assertEqual(loaded["SERPAPI_API_KEY"], "file-key")
                self.assertEqual(os.environ["SERPAPI_API_KEY"], "existing-key")


if __name__ == "__main__":
    unittest.main()
