# Beacon Design Log

## Current Design

Beacon is currently a local, deterministic job-alert pipeline. Gmail is the event source, SQLite is the durable state, rule-based parsing/scoring decides what is worth attention, and Telegram is the alert/reply loop.

The main CLI entrypoint is `beacon.main:main()`, usually run as:

```powershell
$env:PYTHONPATH='src'
python -m beacon.main <command>
```

The most important command paths are:

- `run-local`: runs the same pipeline against `samples/emails`.
- `run-gmail`: runs the same pipeline against Gmail IMAP.
- `run-cycle`: scheduled end-to-end loop for Gmail scan, rescoring, enrichment, Telegram send, and Telegram reply polling.
- `list-jobs`: compact review table from SQLite.
- `digest`: local text digest of recent high-priority jobs.
- `send-telegram-digest`: Telegram version of the digest.
- `show-job`: detailed view of one stored job.
- `update-status`: manual workflow status update.
- `resolve-canonical-urls`: search for public company/ATS job URLs.
- `fetch-job-descriptions`: fetch and store full public job descriptions.

```text
Gmail or local fixtures
  -> SourceEmail records
  -> cheap job/noise filter
  -> deterministic parser
  -> in-memory dedupe
  -> rule-based scorer
  -> SQLite upsert by stable job fingerprint
  -> CLI digest / Telegram digest
  -> optional status replies from Telegram
```

The scheduled automation command is `run-cycle`:

```text
scan Gmail
  -> refresh stored scores and expiry rules
  -> optionally fetch full job descriptions
  -> send Telegram digest
  -> poll Telegram for status commands
```

The active data model is intentionally small:

- `SourceEmail`: raw-ish email input after it enters Beacon.
- `JobOpportunity`: normalized job fields extracted from an email.
- `ScoredJob`: job plus score, category, and explanation.
- SQLite `jobs`: persisted job metadata, scoring output, workflow status, canonical URL, fetched description, and seen counts.

## End-to-End Flow Chart

```text
CLI command
  |
  | run-local
  |   -> run_local()
  |   -> LocalFixtureEmailSource.load_emails()
  |
  | run-gmail
  |   -> run_gmail()
  |   -> GmailImapEmailSource.load_emails()
  |
  v
run_pipeline(email_source, source_label)
  |
  v
email_source.load_emails()
  |
  v
list[SourceEmail]
  |
  v
is_likely_job_email(email)
  |
  v
candidate SourceEmail records
  |
  v
parse_email(email)
  |
  | normal email
  |   -> _extract_title()
  |   -> _extract_company()
  |   -> _extract_labeled_value()
  |   -> _extract_work_mode()
  |   -> _extract_required_skills()
  |   -> _extract_first_url()
  |
  | LinkedIn digest email
  |   -> _extract_linkedin_digest_jobs()
  |   -> _linkedin_job_urls()
  |   -> _linkedin_digest_title()
  |   -> _linkedin_digest_company()
  |   -> _linkedin_digest_location()
  |
  v
list[JobOpportunity]
  |
  v
dedupe_jobs(jobs)
  |
  v
deduped list[JobOpportunity]
  |
  v
score_job(job)
  |
  | -> normalize_job()
  | -> _mark_old_posting_expired()
  | -> _score_role()
  | -> _score_location()
  | -> _score_skills()
  | -> _score_domain()
  | -> _score_company()
  | -> _score_strategic_next_step()
  | -> _score_seniority()
  | -> _score_salary()
  | -> _score_penalties()
  | -> _category_for_score()
  |
  v
list[ScoredJob]
  |
  v
initialize_storage()
  |
  | -> _create_schema()
  | -> _migrate_schema()
  |
  v
upsert_scored_jobs(scored_jobs, connection)
  |
  | -> normalize_scored_job()
  | -> job_fingerprint()
  | -> INSERT ... ON CONFLICT(job_fingerprint) DO UPDATE
  |
  v
SQLite jobs table
  |
  v
render_digest(scored_jobs)
```

## Data Transformations

### 1. Email Loading

The pipeline starts from an `EmailSource` protocol in `src/beacon/email_sources.py`.

Implementations:

- `LocalFixtureEmailSource.load_emails()` reads local `.txt` fixtures through `load_email_fixtures()`.
- `GmailImapEmailSource.load_emails()` reads Gmail via IMAP and converts raw RFC822 messages into `SourceEmail`.

Gmail-specific helpers:

- `_settings_from_env()` loads `.env` values such as `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `GMAIL_IMAP_MAILBOX`, `GMAIL_IMAP_LABEL_MAILBOXES`, `GMAIL_IMAP_SEARCH`, and `GMAIL_IMAP_MAX_RESULTS`.
- `_mailboxes_to_scan()` scans configured label mailboxes first, then the main mailbox.
- `_fetch_header_source_email()` fetches cheap headers first so obvious non-job messages can be skipped before downloading full bodies.
- `_message_to_source_email()` converts Gmail messages into the normalized `SourceEmail` model.
- `_extract_message_body()` prefers `text/plain` and falls back to `text/html`.

Output:

```text
SourceEmail(
  source_id,
  subject,
  sender,
  received_at,
  body
)
```

### 2. Email Filtering

`run_pipeline()` calls `is_likely_job_email(email)` before parsing.

This function combines:

- sender markers such as LinkedIn, Greenhouse, Lever, Ashby, Workday, recruiter-like senders, and careers domains;
- subject/body markers such as `job alert`, `is hiring`, `role:`, `company:`, `apply:`, and target role titles;
- obvious non-job markers such as security alerts, receipts, application confirmations, event emails, and account messages;
- recruiter allowlists from `.env` through `KNOWN_RECRUITER_NAMES` and `KNOWN_RECRUITER_EMAILS`.

Design intent:

- Keep parsing permissive.
- Use filtering to prevent inbox noise from becoming fake low-quality jobs.

### 3. Parsing

`parse_email(email, now=None)` returns `list[JobOpportunity]` because one email may contain multiple jobs.

First branch:

- `_extract_linkedin_digest_jobs()` handles LinkedIn multi-job digest emails.
- If it recognizes a digest, it extracts one `JobOpportunity` per LinkedIn job card.

LinkedIn digest extraction uses:

- `_linkedin_job_urls()` and `_linkedin_job_url_from_line()` to locate job cards.
- `_clean_linkedin_digest_line()` to remove URLs and card chrome.
- `_is_linkedin_digest_noise()` to remove LinkedIn UI text.
- `_linkedin_digest_title()` to find the role title.
- `_linkedin_digest_company()` to find nearby company text.
- `_linkedin_digest_location()` to find nearby location/work-mode text.
- `_linkedin_digest_posted_date()` and `_posted_age_from_email_date()` for freshness metadata.

Fallback branch for ordinary job/recruiter/company emails:

- `_extract_title()` checks `Role:` labels, recruiter prose, `Company is hiring a Role`, contract-opportunity subjects, `Title at Company`, and `Title - Company`.
- `_extract_company()` checks `Company:` labels, body prose, hiring snippets, contract subjects, sender domains, and known signature lines.
- `_extract_labeled_value()` reads inline and block-style fields such as `Location`, `Salary`, and `Role`.
- `_extract_work_mode()` classifies `Remote`, `Hybrid`, or `On-site`.
- `_extract_seniority()` infers `Staff`, `Senior`, or `Junior`.
- `_extract_required_skills()` and `_extract_preferred_skills()` match known AI/ML/data tooling keywords.
- `_extract_first_url()` stores the first URL as the initial apply/source URL.
- `_posted_age()` stores posting age using body text and email timestamp.
- `_is_expired()` marks explicit expired/closed postings.

Output:

```text
JobOpportunity(
  company,
  title,
  location,
  work_mode,
  salary_range,
  seniority,
  required_skills,
  preferred_skills,
  job_link,
  source_email,
  posted_date,
  is_expired
)
```

### 4. Normalization

Normalization is used before scoring and before persistence.

Core functions:

- `normalize_company(value)`
- `normalize_title(value)`
- `normalize_job(job)`
- `normalize_scored_job(scored_job)`

Design intent:

- Make company/title display cleaner.
- Preserve AI/ML acronyms such as `AI`, `ML`, `LLM`, `RAG`, and `MLOps`.
- Canonicalize known companies such as `Cohere`, `Dayforce`, `MongoDB`, `StackAdapt`, `Wealthsimple`, and `Thomson Reuters`.
- Avoid letting noisy subject prefixes become durable job titles.

### 5. Dedupe

There are two dedupe layers.

In-memory run dedupe:

- `dedupe_jobs(jobs)`
- `job_identity_key(job)`

Identity fields:

```text
normalized company
normalized title
normalized location
normalized job_link
```

Cross-run database dedupe:

- `job_fingerprint(scored_job)`
- `_upsert_scored_job(scored_job, connection, timestamp)`

The database stores a SHA-256 fingerprint from the same normalized identity fields. Repeated sightings update `last_seen_at`, increment `seen_count`, and refresh parsed/scored fields instead of inserting a duplicate row.

### 6. Scoring

`score_job(job, preferences=DEFAULT_PREFERENCES)` is additive, explainable, and clamped to `0-100`.

Scoring steps:

- `normalize_job(job)`
- `_mark_old_posting_expired(job)`
- personal blacklist check
- `_score_role(job, preferences)`
- `_score_location(job, preferences)`
- `_score_skills(job, preferences)`
- `_score_domain(job, preferences)`
- `_score_personal_company(job, preferences)`
- `_score_company(job, preferences)`
- `_score_strategic_next_step(job, preferences)`
- `_score_seniority(job)`
- `_score_salary(job)`
- `_fresh_posting_reason(job)`
- `_score_penalties(job)`
- `_category_for_score(score, preferences)`

Current scoring dimensions:

- role/title fit;
- location, currently neutral because extraction is incomplete;
- target AI/ML skill overlap;
- relevant data-science domains such as experimentation, forecasting, personalization, recommendations, growth, and search;
- company tier preferences;
- personal whitelist/blacklist;
- strategic fit toward Applied AI, AI systems, LLM workflows, MLOps, and architecture;
- seniority;
- salary;
- penalties for contract, relocation, on-site, analyst scope, missing company, missing apply link, and stale postings.

Output:

```text
ScoredJob(
  job,
  score,
  category,      # Apply now, Investigate, Skip
  explanation
)
```

### 7. Storage

SQLite is initialized through `initialize_storage()`.

Schema setup:

- `_create_schema(connection)` creates the `jobs` table.
- `_migrate_schema(connection)` adds new columns to older local databases.

Persistence:

- `upsert_scored_jobs(scored_jobs, connection)`
- `_upsert_scored_job(scored_job, connection, timestamp)`

Important stored fields:

- identity and history: `job_fingerprint`, `first_seen_at`, `last_seen_at`, `seen_count`;
- normalized job facts: `company`, `title`, `location`, `work_mode`, `salary_range`, `salary_estimate`, `seniority`, skills JSON, `job_link`, `source_url`, `canonical_url`;
- enrichment: `job_description`, `job_description_url`, `job_description_fetched_at`, `job_description_error`, `description_status`, `description_source`;
- scoring/workflow: `is_expired`, `score`, `category`, `explanation`, `status`;
- timestamps: `created_at`, `updated_at`.

Workflow status is changed separately with:

- `update_job_status(connection, job_id, status)`
- `set_job_status(job_id, status)`
- `_handle_telegram_command(text)`

Supported statuses:

```text
New
Reviewed
Applied
Skipped
Follow-up needed
```

### 8. Description Enrichment

Description enrichment is separate from the core email pipeline.

Canonical URL resolution:

- `resolve_canonical_urls(limit, include_investigate, force)`
- `_should_resolve_canonical_url(row, include_investigate, force)`
- `resolve_source_job_url(source_url)`
- `resolve_canonical_job_url(company, title, location)`

Search providers:

- `SerperSearchProvider`
- `SerpApiSearchProvider`
- `BraveSearchProvider`
- `GoogleCustomSearchProvider`

Candidate scoring:

- `_canonical_search_queries(company, title, location)`
- `_score_canonical_candidate(result, company, title)`
- `_is_rejected_job_url(url)` rejects LinkedIn and third-party job aggregators.

Description fetching:

- `fetch_job_descriptions(limit, include_investigate, force, timeout)`
- `_should_fetch_job_description(row, include_investigate, force)`
- `_description_fetch_target(row)`
- `fetch_job_description(url, timeout)`
- `update_job_description(...)`

Page extraction:

- `extract_job_page_text(html, url, extractor)`
- `extract_ats_job_text(html)`
- `_extract_structured_job_posting_text(html)` for JSON-LD `JobPosting`
- `extract_readability_text(html)`
- `extract_selectolax_text(html)`
- `_description_quality_error(text, url)`

LinkedIn handling:

- `is_linkedin_job_url(url)` detects LinkedIn job URLs.
- `resolve_source_job_url(url)` marks direct LinkedIn fetches as `linkedin_blocked`.
- If a canonical URL exists, Beacon fetches that instead.
- If only a LinkedIn URL exists, Beacon stores a blocked/failure status rather than scraping the auth-walled page.

### 9. Digest and Notification

Local digest:

- `render_digest(scored_jobs)`
- `digest_jobs(since_hours, limit, include_investigate)`
- `_render_stored_digest(rows, since_hours, include_investigate)`

Telegram digest:

- `send_telegram_digest(...)`
- `_recent_digest_rows(...)`
- `_render_telegram_digest(...)`
- `send_telegram_message(text)`
- `_split_telegram_text(text)`

Telegram reply loop:

- `poll_telegram(limit, timeout)`
- `fetch_telegram_updates(...)`
- `_parse_telegram_status_command(text)`
- `_handle_telegram_command(text)`
- `_read_telegram_offset()` and `_write_telegram_offset()` prevent reprocessing old Telegram updates.

Digest filters usually require:

- category is `Apply now`, or optionally `Investigate`;
- status is `New`;
- job is not expired;
- first seen within the configured time window;
- score meets the optional minimum;
- `seen_count` is below the configured max.

### 10. Maintenance Commands

The CLI includes repair and cleanup commands for evolving parser/scorer rules:

- `rescore_stored_jobs(apply=False)` previews or applies the latest scoring rules to stored rows.
- `normalize_stored_jobs(apply=False)` previews or applies company/title normalization updates.
- `repair_hiring_rows(apply=False)` fixes rows where a LinkedIn phrase like `Company is hiring a Role` was parsed into the wrong field.
- `cleanup_non_jobs(apply=False)` previews or deletes obvious non-job rows.
- `cleanup_skipped_jobs(apply=False)` previews or deletes rows categorized as `Skip`.

These commands matter because Beacon is learning from messy inbox data. They let us improve extraction rules without throwing away the local database.

## Current Extraction Strategy

Beacon extracts structured job data from email content first. This keeps the first layer cheap and reliable enough to run often.

Current extraction sources:

- Gmail IMAP messages and configured Gmail labels.
- Local sample email fixtures for tests and development.
- LinkedIn alert text, including multi-job digest cards.
- Recruiter or company emails with labeled fields such as `Role`, `Company`, `Location`, `Salary`, and `Apply`.

Current enrichment sources:

- Public company or ATS job pages.
- Search-provider resolution from LinkedIn/company/title to canonical URLs.
- Public page text extraction through ATS selectors, JSON-LD, `trafilatura`, readability, and visible-text fallback.

Current explicit boundary:

- Beacon does not directly scrape authenticated LinkedIn job pages.
- Direct LinkedIn job URLs are treated as blocked for description fetching unless a canonical company/ATS URL is resolved.

## Decisions So Far

- Start with deterministic parsing and scoring before adding LLM reasoning.
- Use Gmail as the event source, not the primary user interface.
- Keep the pipeline source-agnostic after emails become `SourceEmail` records.
- Store structured facts and workflow state in SQLite.
- Deduplicate both within a run and across runs using normalized company, title, location, and job link fingerprints.
- Preserve workflow status separately from scoring, so rescoring does not erase `Applied`, `Reviewed`, or `Skipped`.
- Treat jobs posted 14+ days ago as expired for action-list purposes.
- Prefer canonical company/ATS job pages over LinkedIn pages for full descriptions.
- Use Telegram for concise high-priority alerts and lightweight status updates.
- Keep `Apply now`, `Investigate`, and `Skip` as the main action categories.
- Add web-search provider support for canonical URL discovery rather than building site-specific scrapers first.

## Known Constraints

- LinkedIn alert emails appear to be daily, not hourly. We have not found a way to increase or change that cadence.
- Because LinkedIn alerts are not timely enough for the 1-2 hour application goal, Gmail alert ingestion alone may miss fresh opportunities.
- Many LinkedIn URLs require login or show auth-wall content, so unauthenticated description fetching is unreliable.
- Search-provider quality affects whether Beacon can find canonical company/ATS postings.
- Location parsing is still incomplete, so scoring currently treats location as neutral.
- The current pipeline does not yet use an LLM for job-description reasoning, resume tailoring, or outreach generation.

## LinkedIn Browser Extraction Option

A logged-in browser extraction layer could improve job-description quality when LinkedIn is the only available source. The safer design is to treat this as a local, user-initiated fallback for selected promising jobs, not as a broad crawler.

Possible flow:

```text
promising job with LinkedIn-only URL
  -> canonical resolver fails
  -> mark as needs_browser_review
  -> optional local logged-in Chrome/Playwright extraction
  -> store description_source = linkedin_browser
```

Design guardrails:

- Use only for a small number of high-value jobs.
- Do not use it as an aggressive background crawler.
- Do not attempt to bypass CAPTCHAs, auth walls, rate limits, or account protections.
- Store provenance clearly so LinkedIn-browser descriptions are distinguishable from public company/ATS descriptions.

## Freshness Problem

Beacon's original goal is to surface strong roles within 1-2 hours of receiving or discovering them. Daily LinkedIn alert cadence conflicts with that goal.

Possible directions:

- Keep LinkedIn alerts as a broad daily discovery source.
- Rely more on company/ATS career pages, search APIs, or targeted job-board queries for fresher discovery.
- Maintain a watchlist of priority companies and search them directly on a schedule.
- Use LinkedIn browser extraction only after a job is discovered, not as the main freshness mechanism.
- Add first-seen freshness and source freshness as separate signals, since "new to Beacon" and "newly posted" are not always the same thing.

## Open Questions

- Should Beacon optimize first for catching every decent job, or only surfacing very high-confidence `Apply now` roles?
- Should `Investigate` mean "maybe worth applying" or "needs more data before scoring properly"?
- Should Telegram be the main daily interface, or just an alert layer while the CLI/database remains primary?
- Should Beacon aggressively delete/clean noisy rows, or keep them as audit history with `Skip`?
- Should company preference outweigh role fit, or should a weak role at a great company still stay low?
- Should location become a real scoring dimension now? It currently returns neutral points because extraction is incomplete.
- When a LinkedIn alert has no canonical job page, should Beacon still notify, or suppress it until it finds a public posting?
- Should the next design direction be better parser/scorer quality, or application assistance with resume tailoring, outreach drafts, and job-detail reasoning?
- Should Beacon add targeted company/ATS monitoring to compensate for LinkedIn's daily alert cadence?
- Should logged-in LinkedIn browser extraction be supported as a manual enrichment command?
