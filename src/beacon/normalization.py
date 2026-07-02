from __future__ import annotations

import re

from .models import JobOpportunity, ScoredJob


COMPANY_CANONICAL_NAMES = {
    "ada": "Ada",
    "ada cx": "Ada CX",
    "bmo": "BMO",
    "cibc": "CIBC",
    "clio": "Clio",
    "cohere": "Cohere",
    "dayforce": "Dayforce",
    "doppel": "Doppel",
    "dropbox": "Dropbox",
    "empire life": "Empire Life",
    "fullscript": "Fullscript",
    "kinaxis": "Kinaxis",
    "mongodb": "MongoDB",
    "reddit": "Reddit",
    "reddit, inc": "Reddit, Inc.",
    "scotiabank": "Scotiabank",
    "shopify": "Shopify",
    "snowflake": "Snowflake",
    "stackadapt": "StackAdapt",
    "td": "TD",
    "thomson reuters": "Thomson Reuters",
    "waabi": "Waabi",
    "wealthsimple": "Wealthsimple",
    "workday": "Workday",
}

TITLE_ACRONYM_REPLACEMENTS = {
    "Ai": "AI",
    "Ml": "ML",
    "Llm": "LLM",
    "Rag": "RAG",
    "Mlop": "MLOp",
    "Mlops": "MLOps",
}


def normalize_company(value: str | None) -> str:
    """Return a consistent display name for a parsed company."""

    if not value:
        return "Unknown"

    cleaned = _normalize_spacing(value)
    cleaned = cleaned.strip(" -*|")
    cleaned = re.sub(r"\s+(?:is hiring|jobs?|careers?|hiring)$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*:\s*(?:up to|update|new jobs?|jobs?|hiring).*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+-\s*(?:up to|jobs?|hiring).*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+and more$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.rstrip(".")

    key = cleaned.casefold().replace(".", "").strip()
    if key in COMPANY_CANONICAL_NAMES:
        return COMPANY_CANONICAL_NAMES[key]
    if _looks_like_company_name(cleaned):
        return _title_preserving_acronyms(cleaned)
    return cleaned


def normalize_title(value: str | None) -> str:
    """Return a clean, consistent job title for display, dedupe, and scoring."""

    if not value:
        return "Unknown role"

    cleaned = _normalize_spacing(value)
    cleaned = cleaned.strip(" -*|")
    cleaned = re.sub(r"^message replied:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^re:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^fw(?:d)?:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^.+?,\s+apply to\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^new jobs similar to\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^.+?,\s+your job(?:'|')?s expiring on .*?:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+-\s+\d+\s*$", "", cleaned)
    cleaned = re.sub(r"\s*\|\s*remote\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*\((?:remote|hybrid|on-site|onsite)\)\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.rstrip(".")

    if cleaned.islower():
        cleaned = cleaned.title()

    return _preserve_role_acronyms(cleaned)


def normalize_job(job: JobOpportunity) -> JobOpportunity:
    """Normalize display identity fields while preserving all extracted metadata."""

    return JobOpportunity(
        company=normalize_company(job.company),
        title=normalize_title(job.title),
        location=job.location,
        work_mode=job.work_mode,
        salary_range=job.salary_range,
        seniority=job.seniority,
        required_skills=job.required_skills,
        preferred_skills=job.preferred_skills,
        job_link=job.job_link,
        source_email=job.source_email,
        posted_date=job.posted_date,
        is_expired=job.is_expired,
    )


def normalize_scored_job(scored_job: ScoredJob) -> ScoredJob:
    """Normalize the nested job while keeping score/category/explanation intact."""

    return ScoredJob(
        job=normalize_job(scored_job.job),
        score=scored_job.score,
        category=scored_job.category,
        explanation=scored_job.explanation,
    )


def _normalize_spacing(value: str) -> str:
    """Collapse whitespace and normalize common pasted punctuation variants."""

    return " ".join(value.replace("\u00a0", " ").split())


def _title_preserving_acronyms(value: str) -> str:
    """Title-case mostly lowercase company text without damaging acronyms."""

    if not value:
        return value
    if any(character.isupper() for character in value[1:]):
        return value
    return _preserve_role_acronyms(value.title())


def _looks_like_company_name(value: str) -> bool:
    """Avoid prettifying arbitrary phrases that were misparsed as companies."""

    if not value or len(value.split()) > 3:
        return False
    if re.search(r"\b(?:you|your|we|can|help|get|noticed|project|ring)\b", value, flags=re.IGNORECASE):
        return False
    return True


def _preserve_role_acronyms(value: str) -> str:
    """Keep common AI/ML acronyms uppercase after title cleanup."""

    for source, target in TITLE_ACRONYM_REPLACEMENTS.items():
        value = re.sub(rf"\b{source}\b", target, value)
    return value
