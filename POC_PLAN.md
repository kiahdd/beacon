# Beacon Local POC Plan

This plan turns the Beacon idea into a local proof of concept that can run before Gmail, cloud deployment, or paid LLM calls are connected.

## POC Objective

Build a local command-line version that reads sample job-alert emails, extracts job opportunities, scores them against career preferences, stores them in SQLite, and prints a ranked digest.

## POC Success Criteria

The POC is successful when we can run one command and see:

- Parsed jobs from local sample email fixtures
- Company, title, location, source, and apply link extracted
- A 0-100 fit score for each job
- Apply now, Investigate, or Skip category
- Results saved locally
- A ranked digest showing the best opportunities first

## Current Status

- Step 1 complete: project skeleton, package entry point, and placeholder `run-local` command are in place.
- Step 4 complete: local sample job-alert fixtures are in `samples/emails/`.
- Step 5 complete: `run-local` loads sample email fixtures from disk.
- Step 6 complete: rule-based parsing extracts structured job opportunities from the sample emails.
- Step 7 complete: deduplication removes repeated job opportunities with a deterministic key.
- Step 8 complete: rule-based scoring assigns score, category, and explanation.
- Step 9 complete: SQLite storage persists scored jobs and prevents cross-run duplicates.
- Step 10 complete: ranked digest shows the best Apply now and Investigate opportunities.
- Step 11 complete: core parser, loader, dedupe, scoring, storage, and digest behavior are covered by tests.
- Step 12 complete: email-source abstraction lets local fixtures and future Gmail share the same pipeline.
- Gmail readiness started: Gmail settings and a GmailEmailSource stub document the next integration point.
- Test coverage started: email fixture loading is covered with standard-library unit tests.

## Executable Steps

### Step 1: Project Skeleton

Create the initial Python project structure.

Files and folders:

- `src/beacon/`
- `src/beacon/__init__.py`
- `src/beacon/main.py`
- `src/beacon/config.py`
- `src/beacon/models.py`
- `src/beacon/parser.py`
- `src/beacon/scorer.py`
- `src/beacon/storage.py`
- `src/beacon/digest.py`
- `samples/emails/`
- `tests/`
- `requirements.txt`

Done means:

- The app can run with `python -m beacon.main`.
- The command prints a placeholder message.

Status: complete.

Local command used during development:

```powershell
$env:PYTHONPATH = "src"
python -m beacon.main run-local
```

### Step 2: Career Preferences Config

Encode the initial role, location, skill, and scoring preferences.

Done means:

- Preferences live in one config module.
- Role keywords, location preferences, skill keywords, and score thresholds are easy to edit.

### Step 3: Data Models

Create structured models for parsed emails and jobs.

Minimum models:

- `SourceEmail`
- `JobOpportunity`
- `ScoredJob`

Done means:

- The app has typed objects for job data instead of loose dictionaries.
- Required fields match the README spec where possible.

### Step 4: Sample Email Fixtures

Add a small set of local sample job-alert emails.

Fixture examples:

- Strong Applied AI role in Toronto or Remote Canada
- Good ML Engineer role with missing salary
- Weak or irrelevant role that should be skipped

Done means:

- Sample files exist in `samples/emails/`.
- The POC can run without Gmail access.

Status: complete.

### Step 5: Local Email Loader

Read sample email fixture files from disk.

Done means:

- The app loads all sample emails.
- Each fixture becomes a `SourceEmail`.
- The CLI prints how many emails were loaded.

Status: complete.

### Step 6: Rule-Based Parser

Extract job fields from sample email text.

Minimum extracted fields:

- Company
- Title
- Location
- Remote, hybrid, or on-site
- Salary range, if available
- Required or mentioned skills
- Apply URL
- Source email
- Posted date, if available

Done means:

- Each sample email produces at least one `JobOpportunity`.
- Missing fields are represented cleanly as empty values or `None`.

Status: complete.

### Step 7: Deduplication

Remove duplicate jobs using a deterministic key.

Suggested key:

- Company
- Title
- Location
- Apply URL

Done means:

- Duplicate sample jobs collapse into one result.
- The CLI reports how many duplicates were removed.

Status: complete.

### Step 8: Rule-Based Scoring

Score jobs from 0-100 with explainable rules.

Initial scoring components:

- Role-title alignment
- Location alignment
- Skill alignment
- Seniority alignment
- Salary signal
- Work-life balance signal
- Negative signals

Done means:

- Every job receives a numeric score.
- Every score includes a short explanation.
- Categories are assigned from the score and signals.

Status: complete.

### Step 9: SQLite Storage

Persist scored jobs in a local SQLite database.

Suggested path:

- `data/beacon.db`

Done means:

- The database is created automatically.
- Jobs are upserted without creating duplicates.
- Status defaults to `New`.

Status: complete.

### Step 10: Ranked Digest

Generate a local text digest.

Digest should include:

- Rank
- Score
- Category
- Company
- Title
- Location
- Link
- Short fit explanation

Done means:

- The CLI prints a ranked digest sorted by score descending.
- Apply now and Investigate jobs are easy to scan.

Status: complete.

### Step 11: Basic Tests

Add focused tests for parsing, scoring, deduplication, and storage.

Done means:

- Tests can run locally.
- Tests cover at least one strong match, one investigate match, and one skip match.

Status: complete.

Current coverage:

- Email fixture loading
- Rule-based parser
- Job deduplication
- Rule-based scoring
- SQLite storage and upsert behavior
- Ranked digest rendering

### Step 12: Gmail Readiness Layer

Add an interface boundary so Gmail ingestion can replace local fixtures later.

Done means:

- The app has an email-source abstraction.
- Local fixtures are one implementation.
- Gmail can be added without rewriting parsing, scoring, storage, or digest code.

Status: complete.

## First Command Target

The first useful command should be:

```bash
python -m beacon.main run-local
```

Expected behavior:

1. Load local sample emails.
2. Parse job opportunities.
3. Deduplicate jobs.
4. Score and categorize jobs.
5. Save results to SQLite.
6. Print a ranked digest.

## Out of Scope for Local POC

These are intentionally deferred until the local loop works:

- Gmail OAuth
- Live Gmail API ingestion
- LLM extraction
- RAG/vector database
- Resume tailoring with real documents
- Chat UI
- SMS, WhatsApp, or push notifications
- Cloud deployment

## Next Milestone: Gmail Integration

The next integration milestone should:

1. Add Google API dependencies.
2. Create Google Cloud OAuth credentials outside the repo.
3. Store OAuth credentials under `secrets/google_oauth_credentials.json`.
4. Store Gmail token cache under `secrets/gmail_token.json`.
5. Implement `GmailEmailSource.load_emails()`.
6. Convert Gmail messages into `SourceEmail` objects.
7. Run the existing Beacon pipeline without changing parser, scorer, storage, or digest code.
