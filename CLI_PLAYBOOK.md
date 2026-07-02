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

The table is designed for scanning. Unknown salary/type values are shown as `-`,
posted ages are shortened like `37m`, `11d`, or `2mo`, and `Exp` shows whether
Beacon considers the job expired.

Show the highest-priority recent jobs:

```powershell
python -m beacon.main digest
```

Include `Investigate` jobs or change the time window:

```powershell
python -m beacon.main digest --include-investigate
python -m beacon.main digest --since-hours 12 --limit 5
```

## Inspect and Update Jobs

Show full details for one job:

```powershell
python -m beacon.main show-job <job_id>
```

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
