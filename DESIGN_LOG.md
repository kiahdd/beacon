# Beacon Design Log

## Current Design

Beacon is currently a local, deterministic job-alert pipeline. Gmail is the event source, SQLite is the durable state, rule-based parsing/scoring decides what is worth attention, and Telegram is the alert/reply loop.

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
