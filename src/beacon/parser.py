from __future__ import annotations

import re
from datetime import UTC, date, datetime

from .config import DEFAULT_PREFERENCES
from .models import JobOpportunity, SourceEmail
from .normalization import normalize_company, normalize_title

TECH_SKILLS = (
    "Python",
    "SQL",
    "Spark",
    "Databricks",
    "MLflow",
    "Kubernetes",
    "Feature Engineering",
    "Experimentation",
    "Recommendation Systems",
    "Recommendations",
    "LLM",
    "Agentic AI",
    "AI Agents",
    "Retrieval-Augmented Generation",
    "RAG",
    "Model Monitoring",
    "MLOps",
    "LangGraph",
    "MCP",
    "OpenAI",
    "Distributed systems",
    "Forecasting",
    "ML Platform",
    "Production ML",
    "Personalization",
    "Bayesian methods",
    "Airflow",
    "AI platform",
    "Feature stores",
    "Model deployment",
    "Evaluation Frameworks",
    "Evaluation pipelines",
    "Tool Calling",
    "Tool use",
    "Enterprise AI",
    "Distributed inference",
    "Prompt optimization",
    "AI safety",
    "Vector databases",
    "Excel",
    "PowerPoint",
    "Tableau",
    "Meta Ads",
    "Google Ads",
    "Attribution",
)


def parse_email(email: SourceEmail, now: datetime | None = None) -> list[JobOpportunity]:
    """Parse a source email into zero or more job opportunities.

    The local POC assumes one job per fixture. Gmail integration can later pass
    one email at a time through this same boundary.
    """
    # Title and company are pulled first because several fallback rules use the
    # subject line, sender, and email signature differently depending on what is
    # already known.
    title = _extract_title(email)
    company = _extract_company(email, title)

    # If we cannot identify either a role or a company, the email is probably
    # not a usable job alert for this rule-based POC.
    if not title and company == "Unknown":
        return []

    title = title or "Unknown role"
    body = email.body

    job = JobOpportunity(
        company=company,
        title=title,
        location=_extract_labeled_value(body, "Location"),
        work_mode=_extract_work_mode(body),
        salary_range=_extract_labeled_value(body, "Salary"),
        seniority=_extract_seniority(title, body),
        required_skills=_extract_required_skills(body),
        preferred_skills=_extract_preferred_skills(body),
        job_link=_extract_first_url(body),
        source_email=email.sender,
        posted_date=_posted_age(email, now),
        is_expired=_is_expired(email, now),
    )
    return [job]


def _extract_title(email: SourceEmail) -> str | None:
    """Infer the role title from explicit labels, body text, or subject."""

    # Many job platforms include `Role: ...`; this is the cleanest signal, so
    # it wins over subject-line guessing.
    role = _extract_labeled_value(email.body, "Role")
    if role:
        return _clean_title(role)

    # Recruiter emails often use prose instead of labels, e.g.
    # "We're hiring a Senior Machine Learning Engineer on our AI Platform team."
    hiring_match = re.search(
        r"we(?:'re| are) hiring (?:a |an )?(?P<title>.+?)(?: on| for|\.|\n)",
        email.body,
        flags=re.IGNORECASE,
    )
    if hiring_match:
        return _clean_title(hiring_match.group("title"))

    subject = _strip_subject_prefix(email.subject)
    company_hiring_match = _company_is_hiring_match(subject) or _company_is_hiring_match(email.body)
    if company_hiring_match:
        return _clean_title(company_hiring_match.group("title"))

    contract_match = _contract_opportunity_match(subject)
    if contract_match:
        return _clean_title(contract_match.group("title"))

    # LinkedIn-style subject: "Senior Machine Learning Engineer at Dayforce".
    if " at " in subject.lower():
        return _clean_title(re.split(r"\s+at\s+", subject, flags=re.IGNORECASE)[0])
    # Other fixtures use a simple "Title - Company" subject shape.
    if " - " in subject:
        return _clean_title(subject.split(" - ", 1)[0])
    # Recruiter outreach can be vague. Preserve the useful domain signal rather
    # than pretending the exact role title is known.
    if "opportunity" in subject.lower() and "applied ai" in subject.lower():
        return "Applied AI role"
    return _clean_title(subject) if subject else None


def _extract_company(email: SourceEmail, title: str | None) -> str:
    """Infer the company from labels, subject, sender, or signature."""

    # Structured job alerts often include a Company field. Prefer it whenever
    # available because it is less ambiguous than sender or subject parsing.
    company = _extract_labeled_value(email.body, "Company")
    if company:
        return company

    body_company = _company_from_body(email.body)
    if body_company:
        return body_company

    subject = _strip_subject_prefix(email.subject)
    company_hiring_match = _company_is_hiring_match(subject) or _company_is_hiring_match(email.body)
    if company_hiring_match:
        return _clean_company(company_hiring_match.group("company"))

    contract_match = _contract_opportunity_match(subject)
    if contract_match:
        return _clean_company(contract_match.group("company"))

    # Subject shape: "Senior Machine Learning Engineer at Dayforce".
    at_match = re.search(r"\s+at\s+(?P<company>.+)$", subject, flags=re.IGNORECASE)
    if at_match:
        return _clean_company(at_match.group("company"))
    # Subject shape: "Applied AI Engineer - Shopify". We avoid treating
    # "Data Scientist - Marketing" as a company name.
    if " - " in subject:
        possible_company = subject.split(" - ", 1)[1]
        if possible_company and "marketing" not in possible_company.lower():
            return _clean_company(possible_company)

    # Recruiter emails may reveal the company through the email domain.
    sender_company = _company_from_sender(email.sender)
    if sender_company:
        return sender_company

    # Some recruiter messages end with a plain company signature line.
    signature_company = _last_known_company_line(email.body)
    if signature_company:
        return signature_company

    return "Unknown"


def _extract_labeled_value(text: str, label: str) -> str | None:
    """Extract `Label: value` or block-style `Label` followed by a value."""

    # Inline format:
    # Location: Remote Canada
    inline_match = re.search(
        rf"^{re.escape(label)}\s*:\s*(?P<value>.+)$",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if inline_match:
        return inline_match.group("value").strip()

    lines = text.splitlines()
    for index, line in enumerate(lines):
        # Block format:
        # Location
        #
        # Toronto
        if line.strip().lower() == label.lower():
            next_value = _next_nonempty_line(lines, index + 1)
            if next_value:
                return next_value
    return None


def _extract_work_mode(text: str) -> str | None:
    """Classify remote, hybrid, or on-site signals from location/body text."""

    lower_text = text.lower()
    location = _extract_labeled_value(text, "Location") or ""
    lower_location = location.lower()

    if "hybrid" in lower_location or "hybrid" in lower_text:
        return "Hybrid"
    if "remote" in lower_location or "remote" in lower_text:
        return "Remote"
    if "on-site" in lower_text or "onsite" in lower_text:
        return "On-site"
    return None


def _extract_seniority(title: str, text: str) -> str | None:
    """Infer seniority from title and experience hints."""

    lower = f"{title}\n{text}".lower()
    if "staff" in lower:
        return "Staff"
    if "senior" in lower:
        return "Senior"
    if "junior" in lower or "1-2 years" in lower:
        return "Junior"
    if re.search(r"(?:-\s*|level\s*)3\b", lower):
        return "Senior"
    if "8+ years" in lower or "5+ years" in lower:
        return "Senior"
    return None


def _extract_required_skills(text: str) -> tuple[str, ...]:
    """Return mentioned skills excluding skills found in preferred sections."""

    preferred = set(_extract_preferred_skills(text))
    return tuple(skill for skill in _extract_skills(text) if skill not in preferred)


def _extract_preferred_skills(text: str) -> tuple[str, ...]:
    """Return skills from optional preference sections like Preferred/Nice to Have."""

    preferred_sections = _section_text(text, ("Preferred", "Nice to Have"))
    if not preferred_sections:
        return ()
    return _extract_skills(preferred_sections)


def _extract_skills(text: str) -> tuple[str, ...]:
    """Find known skill keywords while preserving their configured casing."""

    matches: list[str] = []
    matched_keys: set[str] = set()
    lower_text = text.lower()
    for skill in TECH_SKILLS + tuple(skill.title() for skill in DEFAULT_PREFERENCES.skill_keywords):
        skill_key = skill.lower()
        # `matched_keys` prevents duplicate variants like "LLM" and "Llm" from
        # appearing twice when the built-in skill list overlaps preferences.
        if skill_key in lower_text and skill_key not in matched_keys:
            matches.append(skill)
            matched_keys.add(skill_key)
    return tuple(matches)


def _section_text(text: str, section_names: tuple[str, ...]) -> str:
    """Collect lines from named sections until a likely next section starts."""

    lines = text.splitlines()
    chunks: list[str] = []
    active = False
    stop_words = {"apply", "posted", "salary", "location", "requirements", "responsibilities"}

    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered in {name.lower() for name in section_names}:
            active = True
            continue
        # Stop at common section headers so "Preferred" skills do not swallow
        # unrelated content like Apply links or salary.
        if active and lowered in stop_words:
            active = False
        if active:
            chunks.append(stripped)
    return "\n".join(chunks)


def _extract_first_url(text: str) -> str | None:
    """Return the first URL in an email body, usually the apply link."""

    match = re.search(r"https?://\S+", text)
    if not match:
        return None
    return match.group(0).rstrip(").,")


def _extract_posted_date(text: str) -> str | None:
    """Return the human-readable posted-age text when available."""

    match = re.search(r"Posted\s+(?P<posted>.+?)(?:\.|\n|$)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group("posted").strip()


def _posted_age(email: SourceEmail, now: datetime | None = None) -> str | None:
    """Return current posting age using body text and email timestamp.

    Job alerts often say "Posted 37 minutes ago". That text is relative to when
    the alert email was sent, so storing it literally becomes stale. If Beacon
    has the email timestamp, convert the relative body text into a current age.
    """

    body_age = _extract_posted_date(email.body)
    if body_age and email.received_at:
        posted_at = _estimate_posted_at(email.received_at, body_age)
        if posted_at:
            return _age_text(posted_at, now)
    if body_age:
        return body_age
    return _posted_age_from_email_date(email.received_at, now)


def _posted_age_from_email_date(received_at: datetime | None, now: datetime | None = None) -> str | None:
    """Approximate posting age from the source email timestamp."""

    if received_at is None:
        return None

    return _age_text(received_at, now)


def _estimate_posted_at(received_at: datetime, posted_age: str) -> datetime | None:
    """Estimate absolute posted time from email time plus relative age text."""

    from datetime import timedelta

    lower = posted_age.casefold()
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)

    if "minute" in lower:
        match = re.search(r"(?P<value>\d+)\s+minute", lower)
        minutes = int(match.group("value")) if match else 0
        return received_at - timedelta(minutes=minutes)
    if "hour" in lower:
        match = re.search(r"(?P<value>\d+)\s+hour", lower)
        hours = int(match.group("value")) if match else 0
        return received_at - timedelta(hours=hours)

    match = re.search(r"(?P<value>\d+)\s+(?P<unit>day|days|week|weeks)", lower)
    if not match:
        return None

    value = int(match.group("value"))
    unit = match.group("unit")
    if unit.startswith("week"):
        return received_at - timedelta(days=value * 7)
    return received_at - timedelta(days=value)


def _age_text(reference_time: datetime, now: datetime | None = None) -> str:
    """Return compact age text, preserving useful recent-posting precision."""

    current_time = now or datetime.now(UTC)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=UTC)

    age = current_time - reference_time.astimezone(UTC)
    total_seconds = max(0, int(age.total_seconds()))
    if total_seconds < 120:
        return "1 minute ago"
    if total_seconds < 3600:
        return f"{total_seconds // 60} minutes ago"
    if total_seconds < 7200:
        return "1 hour ago"
    if total_seconds < 86400:
        return f"{total_seconds // 3600} hours ago"

    age_days = max(0, age.days)
    if age_days == 1:
        return "1 day ago"
    return f"{age_days} days ago"


def _is_expired(email: SourceEmail, now: datetime | None = None) -> bool:
    """Detect alerts for jobs that are no longer actionable."""

    text = f"{email.subject}\n{email.body}"
    lower = text.casefold()
    expired_markers = (
        "job expired",
        "job has expired",
        "job is expired",
        "no longer accepting applications",
        "no longer available",
        "position has closed",
        "posting has closed",
        "this job is no longer available",
        "this job posting has expired",
    )
    if any(marker in lower for marker in expired_markers):
        return True

    expiration_date = _extract_expiration_date(text, now)
    if not expiration_date:
        return False

    current_date = (now or datetime.now(UTC)).date()
    return expiration_date < current_date


def _extract_expiration_date(text: str, now: datetime | None = None) -> date | None:
    """Parse simple expiry dates such as `expiring on Jul 2`."""

    match = re.search(
        r"\b(?:expiring|expires|deadline)\s+(?:on\s+)?(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    month_lookup = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    month_text = match.group("month").casefold().rstrip(".")
    month = month_lookup.get(month_text)
    if month is None:
        return None

    current_date = (now or datetime.now(UTC)).date()
    try:
        expiration_date = date(current_date.year, month, int(match.group("day")))
    except ValueError:
        return None

    # Alerts around New Year may refer to a date in the next year.
    if expiration_date.month < current_date.month - 6:
        expiration_date = date(current_date.year + 1, expiration_date.month, expiration_date.day)
    return expiration_date


def _strip_subject_prefix(subject: str) -> str:
    """Remove job-alert boilerplate from subject lines before parsing."""

    cleaned = subject.strip()
    cleaned = re.sub(
        r"^.+?,\s+your job(?:'|')?s expiring on .*?:\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^.+?,\s+apply to\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^new jobs similar to\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    prefixes = (
        "new job",
        "message replied",
        "re",
        "fw",
        "fwd",
    )

    while ":" in cleaned:
        prefix, rest = cleaned.split(":", 1)
        if prefix.strip().casefold() not in prefixes:
            break
        cleaned = rest.strip()

    return cleaned


def _contract_opportunity_match(subject: str) -> re.Match[str] | None:
    """Parse recruiter subjects like `Scotiabank Contract Opportunity ... Data Scientist`."""

    title_pattern = (
        r"(?:Senior\s+|Staff\s+)?(?:Data Scientist|Machine Learning Engineer|"
        r"ML Engineer|AI Engineer|Applied AI Engineer)"
    )
    return re.search(
        rf"^(?P<company>.+?)\s+Contract Opportunity\b.*?\b(?P<title>{title_pattern})\b",
        subject,
        flags=re.IGNORECASE,
    )


def _company_is_hiring_match(text: str) -> re.Match[str] | None:
    """Parse LinkedIn snippets like `StackAdapt is hiring a Senior ML Scientist`."""

    title_pattern = (
        r"(?:Senior/Staff\s+|Senior\s+|Staff\s+)?(?:Applied\s+)?(?:Data Scientist|"
        r"Machine Learning Scientist|Machine Learning Engineer|ML Scientist|"
        r"ML Engineer|AI Engineer|Applied AI Engineer)"
    )
    return re.search(
        rf"(?P<company>[A-Z][A-Za-z0-9&.' -]+?)\s+is hiring\s+(?:a |an )?"
        rf"(?P<title>{title_pattern})(?P<suffix>\s*\([^)]*\))?",
        text,
        flags=re.IGNORECASE,
    )


def _clean_title(value: str) -> str:
    """Trim punctuation that often appears around parsed subject fragments."""

    cleaned = normalize_title(value)
    exciting_match = re.search(
        r"(?:exciting\s+)?(?P<title>(?:senior\s+|staff\s+)?(?:machine learning engineer|"
        r"ml engineer|data scientist|ai engineer|applied ai engineer))\b",
        cleaned,
        flags=re.IGNORECASE,
    )
    if exciting_match:
        return normalize_title(exciting_match.group("title").title())
    return cleaned


def _clean_company(value: str) -> str:
    """Trim punctuation that often appears around parsed company fragments."""

    return normalize_company(value)


def _next_nonempty_line(lines: list[str], start_index: int) -> str | None:
    """Find the next meaningful line after a block-style label."""

    for line in lines[start_index:]:
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _company_from_sender(sender: str) -> str | None:
    """Map known recruiter/company sender domains to company names."""

    lower_sender = sender.lower()
    known_domains = {
        "dayforce": "Dayforce",
        "cohere": "Cohere",
        "mongodb": "MongoDB",
        "wealthsimple": "Wealthsimple",
        "shopify": "Shopify",
    }
    for marker, company in known_domains.items():
        if marker in lower_sender:
            return company
    return None


def _last_known_company_line(text: str) -> str | None:
    """Look for a known company name in the email signature area."""

    known_companies = ("Dayforce", "Cohere", "Shopify", "Clutch", "Wealthsimple", "MongoDB")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if line in known_companies:
            return line
    return None


def _company_from_body(text: str) -> str | None:
    """Infer company from recruiter prose when no explicit Company label exists."""

    contract_match = re.search(
        r"(?:\b\d+\s*-\s*month\s+)?(?P<company>[A-Z][A-Za-z&. ]+?)\s+contract\s+via\s+",
        text,
    )
    if contract_match:
        return _clean_company(contract_match.group("company"))
    return None
