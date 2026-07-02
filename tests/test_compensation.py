from __future__ import annotations

import unittest

from beacon.compensation import estimate_salary, format_salary_estimate


class CompensationTests(unittest.TestCase):
    """Tests for salary estimate helpers used by storage and CLI display."""

    def test_estimates_midpoint_for_salary_range(self) -> None:
        """A listed range should become a simple annual midpoint."""
        self.assertEqual(estimate_salary("CA$180k-250k"), 215_000)
        self.assertEqual(estimate_salary("CA$145,000-210,000"), 177_500)

    def test_estimates_single_salary_value(self) -> None:
        """A single listed salary should be stored directly as the estimate."""
        self.assertEqual(estimate_salary("CA$190,000"), 190_000)

    def test_handles_missing_or_unparseable_salary(self) -> None:
        """Missing or vague compensation text should stay unknown."""
        self.assertIsNone(estimate_salary(None))
        self.assertIsNone(estimate_salary("competitive compensation"))

    def test_formats_estimate_for_cli(self) -> None:
        """CLI output should be compact enough for the jobs table."""
        self.assertEqual(format_salary_estimate(215_000), "CA$215k")
        self.assertEqual(format_salary_estimate(None), "Unknown")


if __name__ == "__main__":
    unittest.main()
