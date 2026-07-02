from __future__ import annotations

import re


def infer_employment_type(text_parts: list[object]) -> str:
    """Infer whether a job appears contract, full-time, or unknown.

    Job alerts do not always expose employment type as a structured field. This
    keeps the first version conservative: only clear text signals produce a
    label, and contract wins over full-time if both appear.
    """

    text = " ".join(str(part or "") for part in text_parts).casefold()
    if _has_contract_signal(text):
        return "Contract"
    if _has_full_time_signal(text):
        return "Full-time"
    return "Unknown"


def _has_contract_signal(text: str) -> bool:
    """Return whether text clearly points to temporary contract work."""

    return any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in (
            r"\bcontract\b",
            r"\bcontractor\b",
            r"\btemporary\b",
            r"\btemp\b",
            r"\b\d+\s*[- ]?\s*month\b",
        )
    )


def _has_full_time_signal(text: str) -> bool:
    """Return whether text clearly points to permanent full-time work."""

    return any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in (
            r"\bfull[ -]?time\b",
            r"\bpermanent\b",
            r"\bfte\b",
        )
    )
