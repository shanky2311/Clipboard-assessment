# bellhaven-sync

Keeps the CRM in line with the Bellhaven Senior Living website. Every day it checks which communities Bellhaven lists, compares them with the CRM accounts, and proposes fixes. A person approves or rejects each fix in a small review app. Nothing is written to the CRM until someone clicks Approve.

## What it checks

| What it finds | What it proposes |
|---|---|
| A community has no CRM account | Create one under Bellhaven with the website details |
| Two or more accounts for one place | Keep one, mark the others Inactive with `duplicate_of_account` set |
| Account sits under another company, with revenue and open AR | CHOW: leave the old account alone, create a new one under Bellhaven, link them with `chow_current_account` |
| Account sits under another company, otherwise | Move it to Bellhaven |
| Account has no parent | Set Bellhaven as the parent |
| Name differs from the website | Rename it |
| Street, zip or city differs | Update the address (flagged if the CRM has a PO box) |
| Care type doesn't fit the website's offerings | Update the care type |
| On the website but not Active | Set it Active |
| Under Bellhaven but not on the website | Set it to Needs Review |

Accounts are matched by address first (state, then street with zip or city), and by name only together with city or zip. A name on its own is never a match.

## Architecture

```
 website ──► scrape.py ──┐
                         ├──► match.py ──► store.py (SQLite) ──► app.py ──► apply.py ──► CRM
 CRM API ──► crm.py ─────┘    proposals     decisions            review     one approved
                                                                 page       change
```

`pipeline.py` runs the left half (read only). `app.py` runs the right half, and only on Approve.

| File | What it does |
|---|---|
| `config.py` | Settings from `.env`, and the CRM field names in one place |
| `scrape.py` | Walks every page of `/communities` and opens each community page for address and care offerings |
| `crm.py` | API client. Pages through accounts, retries reads and updates, never retries a create |
| `normalize.py` | Cleans names and addresses so "Rehab" vs "Rehabilitation" or "St" vs "Street" don't matter |
| `match.py` | Scores and pairs locations with accounts, and builds the proposals. No network or database |
| `store.py` | SQLite file with every proposal, decision and reviewer. Stable keys keep reruns from repeating them |
| `pipeline.py` | The daily job: scrape, read the CRM, match, save. Stops and alerts if the data shrinks suddenly |
| `apply.py` | Writes one approved proposal. Re-checks the account first and refuses if it changed |
| `app.py`, `templates/index.html` | The review page |
| `tests/test_sync.py` | 43 tests with a fake CRM. No internet needed |
| `schedule/crontab.txt`, `.github/workflows/daily-sync.yml` | Ways to run the pipeline on a schedule |

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # put your token in BH_TOKEN

python pipeline.py probe        # check the token and the CRM field names
python -m unittest discover tests
python pipeline.py run          # makes proposals, does not touch the CRM
python app.py                   # open http://127.0.0.1:5000
```

Running `python pipeline.py run` again says `0 new`.

If the website or the CRM returns fewer than 70% of the records from the last run, the run stops without saving anything. Use `python pipeline.py run --force` if the drop is real.

If port 5000 is taken (on macOS, AirPlay uses it), run `PORT=5050 python app.py`.

## Settings

All optional except the token. Set them in `.env` or the environment.

| Variable | Default | What it does |
|---|---|---|
| `BH_TOKEN` | (required) | CRM API token |
| `BH_BASE_URL` | assessment site | Website and API base URL |
| `BELLHAVEN_PARENT_ID` | found by name | The Bellhaven parent account |
| `CHOW_APPLIES_TO_UNPARENTED` | `false` | Use the CHOW rule for accounts with no parent too |
| `BH_DB` | `state/sync.db` | Where proposals and decisions are kept |
| `BH_SNAPSHOTS` | `state/snapshots` | A JSON copy of what each run saw |
| `PORT` | `5000` | Review app port |
| `BH_ALERT_WEBHOOK` | (none) | Slack or other incoming webhook. Gets a message when a run stops or fails |
| `BH_DEBUG` | `false` | Flask debug mode. Only on your own machine |

If the CRM renames a field, change it in `config.F`.

## The review page

- Enter your name at the top before deciding. Every approve and reject records who made it, in the decision file and in the CRM note.
- Proposals are grouped by type, with chips at the top to jump between groups.
- Each card shows the change as old → new, the website and CRM side by side, and why it matched.
- Warnings are shown in red. "Only ones with warnings" filters to those.
- The filter box searches by name, city or account id. Press `/` to jump to it.
- Duplicates, CHOW, new accounts and "not on the website" ask for confirmation before writing.
- Text in the note box is added to the account's CRM note on approve.
- A failed approve shows the error and a Retry button. Rejected items can be reopened.
- Renames can be bulk approved. The button only takes high-confidence ones with no warnings, and each still goes through the same checks. Medium-confidence renames stay for a person.
- "Download CSV" in the Decided list exports every decision (when, who, what, note, result) for an audit trail.

## Safe to rerun

- Each proposal has a key built from what it would change. Decided keys never come back.
- Approve re-reads the account and stops if it changed since the proposal was made.
- Approve claims the proposal first, so a double click can't apply it twice.
- Creating an account first looks for an existing match, so it is never made twice.
- Every change adds a dated line to the account note, except the old account in a CHOW, which is left untouched.

## Schedule

`schedule/crontab.txt` has a daily cron line. `.github/workflows/daily-sync.yml` runs the tests and the pipeline on GitHub Actions. It is manual for now (Actions tab → Run workflow). To make it daily, add `BH_TOKEN` as a repository secret and uncomment the `schedule` lines in the file.
