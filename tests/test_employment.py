from __future__ import annotations

import unittest

from beacon.employment import infer_employment_type


class EmploymentTests(unittest.TestCase):
    """Tests for conservative employment-type inference."""

    def test_detects_contract_roles(self) -> None:
        """Contract language should produce a Contract label."""
        self.assertEqual(
            infer_employment_type(["Data Scientist", "12-month Scotiabank contract"]),
            "Contract",
        )

    def test_detects_full_time_roles(self) -> None:
        """Full-time or permanent language should produce a Full-time label."""
        self.assertEqual(
            infer_employment_type(["Senior AI Engineer", "permanent full-time role"]),
            "Full-time",
        )

    def test_contract_wins_over_full_time_when_conflicting(self) -> None:
        """If signals conflict, Beacon should surface the riskier contract label."""
        self.assertEqual(
            infer_employment_type(["full-time contract opportunity"]),
            "Contract",
        )

    def test_unknown_without_clear_signal(self) -> None:
        """Missing employment-type text should stay unknown."""
        self.assertEqual(infer_employment_type(["Senior Data Scientist"]), "Unknown")


if __name__ == "__main__":
    unittest.main()
