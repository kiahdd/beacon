# Beacon

Beacon is an AI-powered career operating system that continuously monitors job opportunities, ranks them against personalized career preferences, and helps automate the application workflow from discovery to resume tailoring, networking, interview preparation, and application tracking.

The core principle is to reduce job-search noise and help apply quickly to high-quality roles within 1-2 hours of receiving an alert.

## Goals

- Discover relevant job opportunities as quickly as possible.
- Reduce hundreds of job alerts into a small number of high-quality recommendations.
- Apply to high-priority jobs within 1-2 hours of posting or receiving an alert.
- Maintain a searchable knowledge base of applications, resumes, recruiters, networking conversations, and interview history.
- Serve as a long-term AI career assistant, not just a job-alert aggregator.

## Target Job Preferences

Beacon should prioritize roles aligned with:

- Senior Data Scientist
- ML Engineer
- Applied AI
- AI Engineer
- MLOps
- AI Systems

Strong matches should favor:

- Toronto or Remote Canada roles
- ML systems
- Databricks
- Experimentation
- Forecasting
- Recommendation systems
- GenAI
- LLM workflows
- Evaluation frameworks
- AI agents
- Growth toward Senior/Staff-level Applied AI or AI Architecture roles
- Reasonable work-life balance
- Salary above current compensation trajectory when salary data is available

Company preference tiers:

- Tier A: Cohere, Waabi, Shopify, Wealthsimple, StackAdapt, Ada, Workday for AI/ML roles, Snowflake, Thomson Reuters for Applied AI roles
- Tier B: Dropbox, Clio, MongoDB, Dayforce, Kinaxis, Fullscript
- Tier C: large banks, insurance companies, and traditional enterprises only when the specific role is AI/platform-focused

## Architecture

```text
Gmail Job Alerts
  LinkedIn, Greenhouse, Lever, Ashby, company alerts, recruiters
        |
        v
Email Processing Service
        |
        v
Structured Job Extraction
        |
        v
Rule-Based Filtering Engine
        |
        v
AI Job Scoring Engine
        |
        +--------------------+
        |                    |
        v                    v
Structured Database     Vector Database
facts and metadata      RAG knowledge base
        |                    |
        +---------+----------+
                  |
                  v
              Beacon Chat
                  |
                  v
 Resume, Networking, Interview, and Application Agents
```

## Gmail Ingestion

Gmail is the event source, not the primary user interface.

The system should monitor new emails from:

- LinkedIn job alerts
- Company career pages
- Greenhouse
- Lever
- Ashby
- Recruiters
- Referral emails
- Other job boards or alert senders added later

The agent should extract structured information from email content first, avoiding web scraping unless a later feature explicitly requires it.

## Extracted Job Fields

Each job opportunity should be normalized into structured data:

- Company
- Role title
- Location
- Remote, hybrid, or on-site
- Salary range
- Seniority level
- Required skills
- Preferred skills
- Job link
- Source email
- Posted date, if available

## Structured Database

Use a structured database for facts, metadata, statuses, and workflow state.

### Jobs

- Company
- Title
- Location
- Salary
- Remote, hybrid, or on-site
- Seniority
- Skills
- Apply URL
- Posted date
- Source
- AI score
- Category
- Status
- Created timestamp
- Updated timestamp

### Applications

- Job
- Resume version
- Applied date
- Status
- Follow-up date
- Recruiter
- Notes

### Recruiters

- Company
- Contact information
- Previous conversations
- Response history
- Follow-up schedule

### Networking

- Contacts
- Companies
- Coffee chats
- LinkedIn messages
- Follow-up schedule
- Notes

## Status Values

Job and application statuses should support:

- New
- Reviewed
- Applied
- Skipped
- Follow-up needed

## Retrieval-Augmented Generation

Beacon should use retrieval-augmented generation instead of sending full chat history or all stored data to the LLM.

The RAG knowledge base should store:

- Resume versions
- Project descriptions
- STAR interview stories
- Job descriptions
- Company research
- Recruiter conversations
- Networking notes
- Interview feedback
- Cover letters
- Career preferences

Example request:

```text
Tailor my resume for Dayforce.
```

Relevant retrieved context might include:

- Dayforce job description
- Databricks certification
- ML platform project
- Weekly 8M prediction pipeline
- Model deployment experience
- Relevant STAR stories

Only retrieved context should be sent to the LLM, which reduces token usage and cost.

## Scoring

Each opportunity should be scored from 0 to 100.

Beacon should ask a career-specific question, not a generic company question:

```text
Is this company and role the right next step for Kiana?
```

For example, an AI-native ML engineering role with detection systems can score
highly because it moves Kiana toward AI engineering and AI systems work. A
traditional-bank AML contract can score lower even with good compensation if it
does not move her toward the long-term Applied AI / AI architecture direction.

Initial scoring dimensions:

- Career alignment
- Role-title alignment
- Seniority
- Location
- Salary
- Company quality
- AI/ML maturity
- Databricks relevance
- Applied AI relevance
- MLOps relevance
- Experimentation
- Forecasting
- Recommendation systems
- GenAI and LLM workflow relevance
- AI agents
- Long-term career growth
- Work-life balance
- Source quality and freshness

Suggested score interpretation:

- 80-100: strong match
- 60-79: potentially useful match
- 0-59: low-value or noisy match

Long term, every recommendation should include a detailed explanation rather
than only a single score. The goal is to make Beacon's reasoning inspectable:

```text
AI Systems Exposure: 10/10
Technical Team: 9/10
Career Growth: 9/10
Compensation: 8/10
Work-Life Balance: 7/10
Resume Match: 96%
Reason to apply now: Excellent next step toward becoming an Applied AI / ML Systems engineer.
```

## Categories

Each job should be categorized as:

- Apply now
- Investigate
- Skip

Initial category rules:

- Apply now: high score, strong title/domain/location fit, credible link, and no major negative signals
- Investigate: promising but missing salary, unclear seniority, ambiguous location, or incomplete details
- Skip: low score, poor location fit, wrong discipline, junior role, weak relevance, or poor work-life/compensation signal

## Hybrid Rule-Based and LLM Pipeline

To minimize API costs, not every job should be analyzed by an LLM.

Pipeline:

1. Read Gmail.
2. Parse structured fields.
3. Remove duplicates.
4. Apply rule-based filtering.
5. Score using lightweight heuristics.
6. Send only promising jobs to the LLM.
7. Generate action assets for the highest-value opportunities.

Example:

```text
100 incoming alerts
  -> 20 pass keyword filters
  -> 5 exceed score threshold
  -> only those 5 are analyzed by the LLM
  -> top opportunities receive resume tailoring and networking recommendations
```

## Apply Now Output

For each Apply now job, Beacon should generate:

- Short explanation of fit
- Structured recommendation breakdown across AI systems exposure, technical team, career growth, compensation, work-life balance, and resume match
- Clear reason to apply now
- Resume tailoring suggestions
- Recruiter outreach message
- Employee networking message
- Suggested application priority

## Beacon Chat

The primary interface should be conversational rather than a traditional dashboard.

Example interactions:

- Show today's opportunities.
- Why is this job ranked highly?
- Tailor my resume.
- Draft a recruiter message.
- Find employees to network with.
- Prepare interview questions.
- Which companies have ghosted me?
- Show applications awaiting follow-up.
- Compare two job opportunities.

The chat interface should act as an intelligent layer over the structured database and RAG knowledge base.

## Digest and Notifications

Beacon should send a daily or hourly digest showing only the best opportunities.

The digest should focus on:

- Highest-scoring new roles
- Apply now jobs
- Time-sensitive opportunities
- Roles needing a quick investigate decision

Future notification options:

- SMS
- WhatsApp
- Push notifications

Notifications should be reserved for high-scoring opportunities, such as score greater than 90, to reduce alert fatigue.

## Cloud Deployment

Beacon should eventually run in the cloud rather than only on a personal computer.

Possible deployment platforms:

- Render
- Railway
- Google Cloud Run
- AWS Lambda
- Azure Container Apps

Scheduled jobs should periodically monitor Gmail so the system keeps running when the user's computer is offline.

## Long-Term Vision

Beacon should evolve beyond job discovery into a complete AI career operating system.

Future capabilities include:

- Resume optimization
- Explainable recommendations with dimension-level scores and reason-to-apply summaries
- Networking recommendations
- Recruiter relationship management
- Interview preparation
- Company research
- Salary benchmarking
- Application tracking
- Career analytics
- Personalized career planning
- AI-powered career coaching

## Step-by-Step Build Plan

1. Create the project skeleton and local development workflow.
2. Define configuration for Gmail queries, source filters, career preferences, score thresholds, and digest cadence.
3. Create structured models for jobs, applications, recruiters, networking contacts, and messages.
4. Add a local database for the first version.
5. Add sample job-alert fixtures and parser tests.
6. Implement Gmail ingestion for recent job-alert emails.
7. Parse raw emails into candidate job postings.
8. Extract structured job fields using deterministic parsing plus an AI fallback.
9. Deduplicate jobs by company, title, location, and apply URL.
10. Implement the first rule-based scoring rubric.
11. Categorize jobs as Apply now, Investigate, or Skip.
12. Persist normalized jobs, scores, explanations, and statuses.
13. Generate the first ranked digest.
14. Generate action assets for Apply now jobs.
15. Add a basic chat interface over stored jobs and actions.
16. Add RAG for resumes, project stories, job descriptions, and application history.
17. Add cloud scheduling and deployment.

## First Implementation Milestone

The first useful version should:

- Read sample job-alert emails from local fixtures.
- Extract company, title, location, link, and source email.
- Score jobs with a transparent rule-based rubric.
- Store results locally.
- Print a ranked digest of the best opportunities.

After that works, the next milestone is connecting Gmail ingestion.

## Local CLI Commands

Run Beacon against Gmail IMAP using local `.env` credentials:

```powershell
$env:PYTHONPATH='src'
python -m beacon.main run-gmail
```

The local POC uses Gmail IMAP with an app password. By default it searches the
configured mailbox with `GMAIL_IMAP_SEARCH=ALL`, takes the newest
`GMAIL_IMAP_MAX_RESULTS=25` message IDs, filters likely job emails, and then
parses only those candidates. This is count-based rather than date-based. To
look at a different window, update `.env`, for example:

```env
GMAIL_IMAP_SEARCH=ALL
GMAIL_IMAP_MAX_RESULTS=100
```

List stored jobs:

```powershell
python -m beacon.main list-jobs
```

Show recent high-priority jobs only:

```powershell
python -m beacon.main digest
```

By default, `digest` shows `Apply now` jobs added in the last 24 hours. You can
change the time window, limit the number of rows, or include `Investigate` jobs:

```powershell
python -m beacon.main digest --since-hours 12 --limit 5
python -m beacon.main digest --include-investigate
```

Show one stored job:

```powershell
python -m beacon.main show-job <job_id>
```

Update a job status:

```powershell
python -m beacon.main update-status <job_id> reviewed
```

Preview obvious non-job inbox noise that slipped into SQLite:

```powershell
python -m beacon.main cleanup-non-jobs
```

Delete the previewed non-job rows:

```powershell
python -m beacon.main cleanup-non-jobs --apply
```

Preview all rows categorized as `Skip`:

```powershell
python -m beacon.main cleanup-skipped
```

Delete all rows categorized as `Skip`:

```powershell
python -m beacon.main cleanup-skipped --apply
```
