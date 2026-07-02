# Beacon CLI Playbook

Use this playbook when running Beacon locally from PowerShell.

## Start a Session

Set the Python path once per terminal session:

```powershell
$env:PYTHONPATH='src'
```

## Daily Job Scan

Pull recent Gmail job emails, parse them, score them, and store results:

```powershell
python -m beacon.main run-gmail
```

List stored jobs in the compact review table:

```powershell
python -m beacon.main list-jobs
```

The default table is designed for scanning. `Desc` shows whether Beacon has
fetched a full job description: `Y` means fetched, `N` means not fetched, and
`Err` means a fetch was attempted but failed.

Use debug mode when you need the wider diagnostic table:

```powershell
python -m beacon.main list-jobs --debug
```

Debug mode includes salary estimate, employment type, seen count, expiry,
posted age, and other parsing/enrichment signals. Unknown salary/type values are
shown as `-`, posted ages are shortened like `37m`, `11d`, or `2mo`, and `Exp`
shows whether Beacon considers the job expired.

Show the highest-priority recent jobs:

```powershell
python -m beacon.main digest
```

Include `Investigate` jobs or change the time window:

```powershell
python -m beacon.main digest --include-investigate
python -m beacon.main digest --since-hours 12 --limit 5
```

Send a high-priority digest to Telegram:

```powershell
python -m beacon.main send-telegram-digest
```

Tune the Telegram digest:

```powershell
python -m beacon.main send-telegram-digest --include-investigate
python -m beacon.main send-telegram-digest --since-hours 12 --limit 3
python -m beacon.main send-telegram-digest --max-seen-count 2
python -m beacon.main send-telegram-digest --minimum-score 90
```

## Two-Hour Automation Cycle

Run the full Beacon loop manually:

```powershell
python -m beacon.main run-cycle
```

By default this scans Gmail, applies the latest scoring and expiry rules to
stored jobs, fetches full job descriptions for up to 5 promising jobs, sends up
to 5 `Apply now` Telegram jobs first seen in the last 48 hours, excludes jobs
Beacon has already seen 3 or more times, only includes jobs still marked `New`,
and polls Telegram for status replies.

Tune the cycle:

```powershell
python -m beacon.main run-cycle --telegram-limit 3
python -m beacon.main run-cycle --include-investigate
python -m beacon.main run-cycle --max-seen-count 2
python -m beacon.main run-cycle --minimum-score 90
python -m beacon.main run-cycle --description-limit 10
python -m beacon.main run-cycle --skip-description-fetch
python -m beacon.main run-cycle --skip-telegram-poll
```

To schedule it every 2 hours with Windows Task Scheduler:

```text
Program/script:
powershell.exe

Arguments:
-NoProfile -ExecutionPolicy Bypass -Command "cd D:\Kiana\Git\Beacon; $env:PYTHONPATH='src'; python -m beacon.main run-cycle"

Trigger:
Daily, repeat task every 2 hours
```

## Inspect and Update Jobs

Show full details for one job:

```powershell
python -m beacon.main show-job <job_id>
```

If a full description has been fetched, `show-job` prints it after the core job
metadata and scoring explanation.

Update workflow status after reviewing or applying:

```powershell
python -m beacon.main update-status <job_id> reviewed
python -m beacon.main update-status <job_id> applied
python -m beacon.main update-status <job_id> skipped
```

Supported statuses are `new`, `reviewed`, `applied`, `skipped`, and
`follow-up needed`.

## Refresh Existing Rows

Preview rows that would change under the latest scoring rules:

```powershell
python -m beacon.main rescore-stored-jobs
```

Apply the latest scoring rules to existing rows:

```powershell
python -m beacon.main rescore-stored-jobs --apply
```

Use this after changing scoring rules, expiration rules, company preferences, or
salary/employment-type logic. This is the command that updates old rows such as
`129d` postings into expired `Skip` rows.

## Fetch Full Job Descriptions

Before calling Claude, enrich promising stored jobs with the full public posting
text when Beacon has a job URL:

```powershell
python -m beacon.main fetch-job-descriptions
```

By default this fetches up to 5 jobs that are `Apply now`, `New`, not expired,
and have not already had a description stored.

Tune the fetch:

```powershell
python -m beacon.main fetch-job-descriptions --limit 10
python -m beacon.main fetch-job-descriptions --include-investigate
python -m beacon.main fetch-job-descriptions --force
python -m beacon.main fetch-job-descriptions --timeout 30
```

Some sites, especially LinkedIn, may show login/preview pages instead of the
full job description. Beacon stores either the extracted text or the fetch error
so the later Claude step can decide whether it has enough context.

## Repair Stored Data

Preview rows where a LinkedIn phrase like `Company is hiring a Role` was parsed
into the title:

```powershell
python -m beacon.main repair-hiring-rows
```

Apply those company/title repairs and rescore the rows:

```powershell
python -m beacon.main repair-hiring-rows --apply
```

Preview company/title normalization changes:

```powershell
python -m beacon.main normalize-stored-jobs
```

Apply company/title normalization and rescore those rows:

```powershell
python -m beacon.main normalize-stored-jobs --apply
```

## Clean Noise

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

## Gmail Settings

The local POC uses Gmail IMAP with an app password. By default it searches the
configured mailbox with `GMAIL_IMAP_SEARCH=ALL`, takes the newest
`GMAIL_IMAP_MAX_RESULTS=25` message IDs, filters likely job emails, and parses
only those candidates. This is count-based rather than date-based.

To inspect a bigger window, update `.env`:

```env
GMAIL_IMAP_SEARCH=ALL
GMAIL_IMAP_MAX_RESULTS=100
```

List Gmail mailbox names so labels can be configured correctly:

```powershell
python -m beacon.main list-gmail-mailboxes
```

## Telegram Settings

Telegram alerts need these values in `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Start with one-way alerts through `send-telegram-digest`. Full Telegram chat
commands can come later after the notification path is reliable.

## Telegram Status Commands

After receiving a digest, send one of these messages to the bot:

```text
/applied 123
/reviewed 123
/skipped 123
/followup 123
```

Then poll Telegram locally so Beacon can process those commands:

```powershell
python -m beacon.main poll-telegram
```

Beacon stores the latest Telegram update offset in `data/telegram_update_offset.txt`
so the same bot message is not processed repeatedly.
