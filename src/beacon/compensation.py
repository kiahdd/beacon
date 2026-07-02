from __future__ import annotations

import re


def estimate_salary(salary_range: str | None) -> int | None:
    """Estimate annual salary from a parsed salary string.

    Beacon stores the original salary text separately. This helper creates a
    numeric estimate for quick table scanning and future filtering. For ranges,
    it returns the midpoint; for one listed salary, it returns that value.
    """

    if not salary_range:
        return None

    values = [_normalize_salary_match(match) for match in _salary_matches(salary_range)]
    values = [value for value in values if value is not None]
    if not values:
        return None

    if len(values) == 1:
        return values[0]
    return round((min(values) + max(values)) / 2)


def format_salary_estimate(value: int | None) -> str:
    """Format an annual salary estimate for compact CLI display."""

    if value is None:
        return "Unknown"
    return f"CA${round(value / 1000)}k"


def _salary_matches(salary_range: str) -> list[re.Match[str]]:
    """Find salary-like numbers while preserving optional `k` suffixes."""

    return list(
        re.finditer(
            r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>[kK])?",
            salary_range,
        )
    )


def _normalize_salary_match(match: re.Match[str]) -> int | None:
    """Convert one regex match into an annual salary-like integer."""

    raw_amount = match.group("amount").replace(",", "")
    try:
        amount = float(raw_amount)
    except ValueError:
        return None

    suffix = match.group("suffix")
    if suffix or amount < 1000:
        amount *= 1000
    return int(round(amount))
