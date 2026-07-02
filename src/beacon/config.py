from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CareerPreferences:
    """Career matching preferences used by the local scoring pipeline.

    This keeps the first POC simple: preferences are code-level constants for
    now, and can later move to a user-editable TOML/YAML/database record.
    """

    # These keyword groups are intentionally broad. The scorer can use them to
    # identify promising jobs before any expensive LLM analysis happens.
    role_keywords: tuple[str, ...]
    location_keywords: tuple[str, ...]
    skill_keywords: tuple[str, ...]
    domain_keywords: tuple[str, ...] = ()
    tier_a_companies: tuple[str, ...] = ()
    tier_b_companies: tuple[str, ...] = ()
    tier_c_companies: tuple[str, ...] = ()
    ai_native_companies: tuple[str, ...] = ()
    strategic_direction_keywords: tuple[str, ...] = ()
    limited_direction_keywords: tuple[str, ...] = ()

    # Category thresholds are centralized here so tuning the ranking behavior
    # does not require editing scoring logic in multiple places.
    apply_now_threshold: int = 80
    investigate_threshold: int = 60


@dataclass(frozen=True)
class GmailSettings:
    """Configuration needed by the future Gmail email source.

    These values are placeholders for the next integration milestone. Keeping
    them here documents what Gmail ingestion will need without adding OAuth or
    Google API dependencies yet.
    """

    search_query: str
    credentials_path: Path
    token_path: Path
    max_results: int = 25


@dataclass(frozen=True)
class GmailImapSettings:
    """Configuration for Gmail access through IMAP and an app password.

    A Gmail app password cannot be used with the OAuth Gmail API directly. It
    works with IMAP, which is enough for the local POC because Beacon only needs
    to read recent alert emails and normalize them into SourceEmail objects.
    """

    address: str | None
    app_password: str | None
    host: str = "imap.gmail.com"
    mailbox: str = "INBOX"
    label_mailboxes: tuple[str, ...] = ()
    search_criteria: str = "ALL"
    max_results: int = 25


# Default preferences reflect Kiana's target direction for this POC:
# senior/applied AI work, Toronto or remote Canada, and ML systems depth.
DEFAULT_PREFERENCES = CareerPreferences(
    role_keywords=(
        "senior data scientist",
        "staff data scientist",
        "machine learning engineer",
        "machine learning scientist",
        "ml engineer",
        "ml scientist",
        "applied ai",
        "ai engineer",
        "ai platform engineer",
        "mlops",
        "ai systems",
    ),
    location_keywords=(
        "toronto",
        "remote canada",
        "canada remote",
        "canada",
    ),
    skill_keywords=(
        "machine learning",
        "ml systems",
        "databricks",
        "mlflow",
        "kubernetes",
        "experimentation",
        "forecasting",
        "recommendation systems",
        "genai",
        "llm",
        "rag",
        "evaluation",
        "evaluation frameworks",
        "evaluation pipelines",
        "ai agents",
        "agentic",
        "mlops",
    ),
    domain_keywords=(
        "marketing",
        "loyalty",
        "personalization",
        "recommendation",
        "recommendations",
        "growth",
        "retention",
        "lifecycle",
        "crm",
        "customer",
        "shopping",
        "search",
        "pricing",
        "experimentation",
        "causal inference",
        "incrementality",
        "forecasting",
    ),
    tier_a_companies=(
        "cohere",
        "waabi",
        "shopify",
        "wealthsimple",
        "stackadapt",
        "ada",
        "workday",
        "snowflake",
        "thomson reuters",
    ),
    tier_b_companies=(
        "dropbox",
        "clio",
        "mongodb",
        "dayforce",
        "kinaxis",
        "fullscript",
    ),
    tier_c_companies=(
        "scotiabank",
        "td",
        "td bank",
        "rbc",
        "royal bank",
        "bmo",
        "cibc",
        "national bank",
        "manulife",
        "sun life",
        "sunlife",
        "empire life",
        "intact",
        "desjardins",
        "bell",
        "rogers",
        "telus",
        "loblaw",
        "canadian tire",
    ),
    ai_native_companies=(
        "doppel",
        "cohere",
        "waabi",
        "ada",
        "stackadapt",
        "snowflake",
    ),
    strategic_direction_keywords=(
        "ai engineering",
        "ai platform",
        "applied ai",
        "ai systems",
        "machine learning",
        "ml systems",
        "production ml",
        "mlops",
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
    ),
    limited_direction_keywords=(
        "aml",
        "anti-money laundering",
        "compliance",
        "regulatory",
        "reporting",
        "dashboard",
        "business intelligence",
        "tableau",
        "excel",
        "contract",
    ),
)


DEFAULT_GMAIL_SETTINGS = GmailSettings(
    search_query=(
        "newer_than:2d "
        "(from:linkedin.com OR from:greenhouse.io OR from:lever.co OR "
        "from:ashbyhq.com OR subject:(job OR recruiter OR opportunity))"
    ),
    credentials_path=Path("secrets/google_oauth_credentials.json"),
    token_path=Path("secrets/gmail_token.json"),
    max_results=25,
)
