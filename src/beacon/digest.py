from __future__ import annotations

from .models import ScoredJob


DEFAULT_DIGEST_LIMIT = 10
VISIBLE_CATEGORIES = ("Apply now", "Investigate")


def render_digest(
    scored_jobs: list[ScoredJob],
    limit: int = DEFAULT_DIGEST_LIMIT,
    include_skips: bool = False,
) -> str:
    """Render scored jobs into a concise ranked action digest.

    The digest is intentionally more selective than raw CLI debug output. Beacon
    should reduce noise, so skipped jobs are hidden unless explicitly requested.
    """

    if not scored_jobs:
        return "No job opportunities found yet."

    visible_jobs = _visible_jobs(scored_jobs, include_skips=include_skips)[:limit]
    if not visible_jobs:
        return "No Apply now or Investigate opportunities found."

    lines = ["Beacon ranked action digest", ""]
    # The digest is sorted here so callers can pass jobs in any order and still
    # get the highest-value opportunities first.
    for rank, scored in enumerate(visible_jobs, 1):
        job = scored.job
        location = job.location or "Unknown location"
        link = job.job_link or "No job URL found"
        lines.append(f"{rank}. {scored.category} [{scored.score}]")
        lines.append(f"   {job.company} - {job.title}")
        lines.append(f"   Location: {location}")
        lines.append(f"   Link: {link}")
        lines.append(f"   Why: {scored.explanation}")
    return "\n".join(lines)


def _visible_jobs(scored_jobs: list[ScoredJob], include_skips: bool) -> list[ScoredJob]:
    """Sort jobs and optionally filter out skipped opportunities."""

    sorted_jobs = sorted(scored_jobs, key=lambda item: item.score, reverse=True)
    if include_skips:
        return sorted_jobs
    return [job for job in sorted_jobs if job.category in VISIBLE_CATEGORIES]
