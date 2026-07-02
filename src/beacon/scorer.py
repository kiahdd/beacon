from __future__ import annotations

from dataclasses import replace
import re

from .config import CareerPreferences, DEFAULT_PREFERENCES
from .models import JobOpportunity, ScoredJob
from .normalization import normalize_job


# The scoring rubric is additive, then clamped into this range. Keeping the
# bounds explicit makes it clear that penalties can never create negative scores
# and strong matches can never exceed 100.
MAX_SCORE = 100
MIN_SCORE = 0


def score_job(
    job: JobOpportunity,
    preferences: CareerPreferences = DEFAULT_PREFERENCES,
) -> ScoredJob:
    """Score a job opportunity against career preferences.

    The POC scorer is intentionally transparent. It uses cheap deterministic
    signals first, then produces a short explanation so the user can understand
    why a role landed in Apply now, Investigate, or Skip.

    Current rubric:

    - Role/title fit: up to 30 points
    - Location signal: neutral for now while LinkedIn location extraction is incomplete
    - Target skill fit: up to 25 points
    - Relevant DS domain signal: up to 8 points
    - Personal company whitelist: up to 15 points
    - Preferred company tier: up to 10 points
    - Strategic next-step fit: up to 12 points
    - Seniority/growth fit: up to 15 points
    - Salary signal: up to 10 points
    - Penalties and personal blacklist: contract, relocation, and missing/negative
      signals subtract points
    - Expiry handling: explicit expired signals or postings 14+ days old force Skip

    This is deliberately simple and explainable. Once Beacon has real data, we
    can tune weights or add an LLM review only for uncertain/high-potential jobs.
    """

    job = _mark_old_posting_expired(normalize_job(job))

    blacklist_match = _first_company_match(
        _job_searchable_text(job),
        preferences.personal_company_blacklist,
    )
    if blacklist_match:
        return ScoredJob(
            job=job,
            score=MIN_SCORE,
            category="Skip",
            explanation=(
                "company is on personal blacklist: "
                f"{_display_keyword(blacklist_match)}"
            ),
        )

    score = 0
    reasons: list[str] = []

    # Each scoring helper returns both points and a human-readable reason. The
    # reason is just as important as the number because Beacon should be able to
    # explain why a role is worth applying to or skipping.
    role_points, role_reason = _score_role(job, preferences)
    score += role_points
    reasons.append(role_reason)

    location_points, location_reason = _score_location(job, preferences)
    score += location_points
    reasons.append(location_reason)

    skill_points, skill_reason = _score_skills(job, preferences)
    score += skill_points
    reasons.append(skill_reason)

    domain_points, domain_reason = _score_domain(job, preferences)
    score += domain_points
    if domain_reason:
        reasons.append(domain_reason)

    personal_company_points, personal_company_reason = _score_personal_company(job, preferences)
    score += personal_company_points
    if personal_company_reason:
        reasons.append(personal_company_reason)

    company_points, company_reason = _score_company(job, preferences)
    score += company_points
    if company_reason:
        reasons.append(company_reason)

    strategic_points, strategic_reason = _score_strategic_next_step(job, preferences)
    score += strategic_points
    if strategic_reason:
        reasons.append(strategic_reason)

    seniority_points, seniority_reason = _score_seniority(job)
    score += seniority_points
    reasons.append(seniority_reason)

    salary_points, salary_reason = _score_salary(job)
    score += salary_points
    reasons.append(salary_reason)

    fresh_posting_reason = _fresh_posting_reason(job)
    if fresh_posting_reason:
        reasons.append(fresh_posting_reason)

    penalty_points, penalty_reason = _score_penalties(job)
    score += penalty_points
    if penalty_reason:
        reasons.append(penalty_reason)

    if job.is_expired:
        score = MIN_SCORE
        reasons.append("job appears expired or no longer accepting applications")

    score = _clamp_score(score)
    category = _category_for_score(score, preferences)

    return ScoredJob(
        job=job,
        score=score,
        category=category,
        explanation="; ".join(reason for reason in reasons if reason),
    )


def _score_role(job: JobOpportunity, preferences: CareerPreferences) -> tuple[int, str]:
    """Score whether the title matches the target career direction."""

    title = job.title.casefold()
    matched = [keyword for keyword in preferences.role_keywords if keyword in title]

    # Exact keyword hits such as "Senior Data Scientist" or "Applied AI
    # Engineer" are the strongest signal because the user's target role family
    # is clear and specific.
    if matched:
        return 30, f"strong role-title match: {', '.join(matched)}"
    if (
        "data scientist" in title
        or "machine learning engineer" in title
        or "machine learning scientist" in title
        or "ml engineer" in title
        or "ml scientist" in title
    ):
        return 28, "core role-family match"
    # Partial AI/ML/data-science titles can still be relevant, but they need
    # support from location/skills/seniority before becoming high priority.
    if "machine learning" in title or "ai" in title:
        return 22, "partial role-title match"
    # Analyst/marketing roles are usually not the intended career direction.
    if "analyst" in title or "marketing" in title:
        return 0, "role title is outside target roles"
    return 8, "role title has limited target alignment"


def _score_location(job: JobOpportunity, preferences: CareerPreferences) -> tuple[int, str]:
    """Return a neutral location score until location extraction is reliable."""

    return 20, "location not scored yet"


def _score_skills(job: JobOpportunity, preferences: CareerPreferences) -> tuple[int, str]:
    """Score overlap between extracted skills and preferred AI/ML skills."""

    skills = job.required_skills + job.preferred_skills
    skill_text = " ".join(skills).casefold()
    matched = [keyword for keyword in preferences.skill_keywords if keyword in skill_text]

    if not matched:
        return 0, "no target skill match found"

    # Cap skill points so broad job descriptions do not dominate the whole score.
    # Required and preferred skills are both useful at this POC stage because
    # either can reveal the role's technical direction.
    points = min(25, 5 * len(matched))
    return points, f"target skills matched: {', '.join(matched[:5])}"


def _score_domain(job: JobOpportunity, preferences: CareerPreferences) -> tuple[int, str | None]:
    """Boost senior DS roles in domains that are useful for Applied AI growth."""

    if not _is_target_data_science_role(job):
        return 0, None

    searchable_text = " ".join(
        (
            job.title,
            job.company,
            " ".join(job.required_skills),
            " ".join(job.preferred_skills),
        )
    ).casefold()
    matched = [keyword for keyword in preferences.domain_keywords if keyword in searchable_text]
    if not matched:
        return 0, None

    return 8, f"relevant DS domain signal: {', '.join(matched[:4])}"


def _score_company(job: JobOpportunity, preferences: CareerPreferences) -> tuple[int, str | None]:
    """Boost companies Kiana explicitly wants Beacon to prioritize."""

    searchable_text = f"{job.company} {job.title}".casefold()

    tier_a_match = _first_company_match(searchable_text, preferences.tier_a_companies)
    if tier_a_match:
        if tier_a_match in ("workday", "thomson reuters") and not _is_ai_platform_focused(job):
            return 0, f"{_display_keyword(tier_a_match)} needs AI/platform focus for tier A boost"
        return 10, f"tier A company preference: {_display_keyword(tier_a_match)}"

    tier_b_match = _first_company_match(searchable_text, preferences.tier_b_companies)
    if tier_b_match:
        return 6, f"tier B company preference: {_display_keyword(tier_b_match)}"

    tier_c_match = _first_company_match(searchable_text, preferences.tier_c_companies)
    if tier_c_match and _is_ai_platform_focused(job):
        return 3, f"traditional enterprise with AI/platform focus: {_display_keyword(tier_c_match)}"

    return 0, None


def _score_personal_company(
    job: JobOpportunity,
    preferences: CareerPreferences,
) -> tuple[int, str | None]:
    """Apply Kiana-specific company preferences before general company tiers."""

    whitelist_match = _first_company_match(
        _job_searchable_text(job),
        preferences.personal_company_whitelist,
    )
    if whitelist_match:
        return 15, f"personal company whitelist: {_display_keyword(whitelist_match)}"

    return 0, None


def _score_strategic_next_step(
    job: JobOpportunity,
    preferences: CareerPreferences,
) -> tuple[int, str | None]:
    """Score whether this role advances Kiana's long-term AI direction."""

    searchable_text = _job_searchable_text(job)
    ai_native_match = _first_company_match(searchable_text, preferences.ai_native_companies)
    has_strategic_direction = _has_any_keyword(searchable_text, preferences.strategic_direction_keywords)
    has_limited_direction = _has_any_keyword(searchable_text, preferences.limited_direction_keywords)
    tier_c_match = _first_company_match(searchable_text, preferences.tier_c_companies)

    if ai_native_match and _is_target_data_science_role(job) and has_strategic_direction:
        return 14, (
            "strategic next step: "
            f"{_display_keyword(ai_native_match)} moves toward AI engineering"
        )

    if (
        job.job_link
        and _is_target_data_science_role(job)
        and _has_high_intent_ai_direction(searchable_text)
    ):
        return 6, "strategic next step: role builds Applied AI/AI systems direction"

    if tier_c_match and has_limited_direction:
        return -8, (
            "limited strategic movement: "
            f"{_display_keyword(tier_c_match)} role appears enterprise/compliance-focused"
        )

    if has_limited_direction:
        return -4, "limited strategic movement toward long-term AI direction"

    return 0, None


def _is_target_data_science_role(job: JobOpportunity) -> bool:
    """Identify DS/ML roles before applying business-domain boosts."""

    title = job.title.casefold()
    return (
        "data scientist" in title
        or "machine learning" in title
        or "ml engineer" in title
        or "ml scientist" in title
        or "applied ai" in title
        or "ai engineer" in title
    )


def _is_ai_platform_focused(job: JobOpportunity) -> bool:
    """Identify AI/platform-heavy roles at traditional or conditional companies."""

    text = _job_searchable_text(job)
    return any(
        keyword in text
        for keyword in (
            "ai",
            "applied ai",
            "machine learning",
            "ml ",
            "mlops",
            "llm",
            "rag",
            "genai",
            "platform",
            "architecture",
            "architect",
            "databricks",
            "evaluation",
            "agent",
        )
    )


def _has_high_intent_ai_direction(text: str) -> bool:
    """Return whether a role points beyond broad ML into AI systems work."""

    return any(
        keyword in text
        for keyword in (
            "ai engineering",
            "ai platform",
            "applied ai",
            "ai systems",
            "llm",
            "rag",
            "embeddings",
            "guardrails",
            "evaluation",
            "ai agents",
            "agentic",
            "detection systems",
            "recommendation systems",
            "architecture",
            "architect",
        )
    )


def _score_seniority(job: JobOpportunity) -> tuple[int, str]:
    """Score whether the role supports Senior/Staff-level growth."""

    seniority = (job.seniority or "").casefold()
    title = job.title.casefold()

    if "staff" in seniority or "staff" in title:
        return 15, "staff-level growth signal"
    if "senior" in seniority or "senior" in title:
        return 14, "senior-level match"
    if "junior" in seniority or "junior" in title:
        return -12, "junior role is below target seniority"
    if not seniority:
        return 4, "seniority unclear"
    return 6, "seniority has limited target alignment"


def _score_salary(job: JobOpportunity) -> tuple[int, str]:
    """Score compensation signal when a salary range is present."""

    # Missing salary should not kill an otherwise good opportunity. Many
    # Canadian job alerts omit it, so this gets a small neutral-ish score.
    if not job.salary_range:
        return 3, "salary missing"

    high_salary = _max_salary_value(job.salary_range)
    if high_salary is None:
        return 4, "salary listed but could not be parsed"
    if high_salary >= 200_000:
        return 10, "salary signal is strong"
    if high_salary >= 160_000:
        return 7, "salary signal is reasonable"
    if high_salary >= 120_000:
        return 4, "salary signal is moderate"
    return 0, "salary appears below target trajectory"


def _score_penalties(job: JobOpportunity) -> tuple[int, str | None]:
    """Apply negative signals that should reduce priority."""

    penalties: list[str] = []
    points = 0
    searchable_text = _job_searchable_text(job)

    # Penalties are separate from the positive rubric so they are visible in the
    # explanation and easy to tune independently.
    if (job.work_mode or "").casefold() == "on-site":
        points -= 12
        penalties.append("on-site role is a preference mismatch")
    if _has_contract_signal(searchable_text):
        points -= 8
        penalties.append("contract role is less preferred")
    if _has_relocation_signal(searchable_text):
        points -= 15
        penalties.append("relocation requirement is a preference mismatch")
    stale_points, stale_reason = _score_staleness(job)
    points += stale_points
    if stale_reason:
        penalties.append(stale_reason)
    if "analyst" in job.title.casefold():
        points -= 10
        penalties.append("analyst title is below target scope")
    if job.company == "Unknown":
        points -= 8
        penalties.append("company missing")
    if not job.job_link:
        points -= 4
        penalties.append("apply link missing")

    return points, ", ".join(penalties) if penalties else None


def _score_staleness(job: JobOpportunity) -> tuple[int, str | None]:
    """Explain old postings; 14+ day jobs are expired elsewhere."""

    posted_age_days = _posted_age_days(job.posted_date)
    if posted_age_days is None or posted_age_days < 14:
        return 0, None
    return 0, f"posting is more than 2 weeks old: {posted_age_days} days"


def _mark_old_posting_expired(job: JobOpportunity) -> JobOpportunity:
    """Treat postings 14+ days old as expired for action-list purposes."""

    posted_age_days = _posted_age_days(job.posted_date)
    if posted_age_days is None or posted_age_days < 14 or job.is_expired:
        return job
    return replace(job, is_expired=True)


def _fresh_posting_reason(job: JobOpportunity) -> str | None:
    """Highlight jobs posted within the last two hours without changing score."""

    posted_age_minutes = _posted_age_minutes(job.posted_date)
    if posted_age_minutes is None or posted_age_minutes > 120:
        return None
    return "fresh posting: posted within the last 2 hours"


def _posted_age_minutes(posted_date: str | None) -> int | None:
    """Parse minute/hour posted-age text when Beacon has recent precision."""

    if not posted_date:
        return None

    text = posted_date.casefold()
    match = re.search(r"(?P<count>\d+)\s*(?P<unit>minute|minutes|hour|hours)", text)
    if not match:
        return None

    count = int(match.group("count"))
    unit = match.group("unit")
    if unit.startswith("minute"):
        return count
    if unit.startswith("hour"):
        return count * 60
    return None


def _posted_age_days(posted_date: str | None) -> int | None:
    """Parse Beacon's compact posted-age text into days when possible."""

    if not posted_date:
        return None

    text = posted_date.casefold()
    match = re.search(r"(?P<count>\d+)\s*(?P<unit>minute|minutes|hour|hours|day|days|week|weeks|month|months)", text)
    if not match:
        return None

    count = int(match.group("count"))
    unit = match.group("unit")
    if unit.startswith("minute") or unit.startswith("hour"):
        return 0
    if unit.startswith("day"):
        return count
    if unit.startswith("week"):
        return count * 7
    if unit.startswith("month"):
        return count * 30
    return None


def _has_contract_signal(text: str) -> bool:
    """Return whether the role appears to be temporary contract work."""

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


def _has_relocation_signal(text: str) -> bool:
    """Return whether the role appears to require moving to another city."""

    return any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in (
            r"\brelocat(?:e|ion|ing)\b",
            r"\bmust relocate\b",
            r"\bopen to relocation\b",
            r"\brelocation required\b",
        )
    )


def _category_for_score(score: int, preferences: CareerPreferences) -> str:
    """Translate numeric score into the user's action bucket."""

    if score >= preferences.apply_now_threshold:
        return "Apply now"
    if score >= preferences.investigate_threshold:
        return "Investigate"
    return "Skip"


def _max_salary_value(salary_range: str) -> int | None:
    """Parse the highest salary number from common Canadian salary strings."""

    values = [int(match.replace(",", "")) for match in re.findall(r"\d[\d,]*", salary_range)]
    if not values:
        return None

    # `CA$180k-250k` parses as 180 and 250; treat small numbers as thousands.
    # `CA$145,000-210,000` parses as 145000 and 210000 and is left as-is.
    normalized = [value * 1000 if value < 1000 else value for value in values]
    return max(normalized)


def _first_keyword_match(text: str, keywords: tuple[str, ...]) -> str | None:
    """Return the first configured keyword found in normalized text."""

    for keyword in keywords:
        if keyword in text:
            return keyword
    return None


def _first_company_match(text: str, companies: tuple[str, ...]) -> str | None:
    """Return the first company match without matching inside another word."""

    for company in companies:
        pattern = rf"(?<![a-z0-9]){re.escape(company)}(?![a-z0-9])"
        if re.search(pattern, text, flags=re.IGNORECASE):
            return company
    return None


def _has_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    """Return whether normalized text contains any configured keyword."""

    return _first_keyword_match(text, keywords) is not None


def _job_searchable_text(job: JobOpportunity) -> str:
    """Combine job fields used by scoring helpers into normalized text."""

    return " ".join(
        (
            job.company,
            job.title,
            job.location or "",
            job.seniority or "",
            job.work_mode or "",
            " ".join(job.required_skills),
            " ".join(job.preferred_skills),
        )
    ).casefold()


def _display_keyword(keyword: str) -> str:
    """Format configured keywords for human-readable score explanations."""

    special_cases = {
        "rbc": "RBC",
        "bmo": "BMO",
        "cibc": "CIBC",
        "td": "TD",
        "mongodb": "MongoDB",
        "stackadapt": "StackAdapt",
    }
    return special_cases.get(keyword, keyword.title())


def _clamp_score(score: int) -> int:
    """Keep every score inside the public 0-100 range."""

    return max(MIN_SCORE, min(MAX_SCORE, score))
