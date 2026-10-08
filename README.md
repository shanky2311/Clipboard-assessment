# bellhaven-sync

This keeps the CRM in line with the Bellhaven website. It checks which facilities Bellhaven owns today and shows you what in the CRM is out of date. You approve each fix before it goes in.

How it flows:

1. `scrape.py` reads every community from the website.
2. `crm.py` pulls every account from the CRM.
3. `match.py` pairs them up and lists what looks wrong.
4. `store.py` saves that list in a small SQLite file.
5. `app.py` shows you the list. When you click Approve, `apply.py` writes that one change to the CRM.

The pipeline never writes to the CRM by itself. Only the Approve button does.

| File | What it does |
|---|---|
| `config.py` | Settings, and the CRM field names (`account_id`, `billing_street`, ...) in one place |
| `scrape.py` | Walks every page of `/communities` and opens each community page |
| `crm.py` | API client. Pages through accounts and retries reads and updates, but never a create |
| `normalize.py` | Cleans names and addresses so "Rehab" vs "Rehabilitation" or "St" vs "Street" don't matter |
| `match.py` | Pairs locations with accounts and builds the proposals. No network, no database |
| `store.py` | SQLite file with every proposal and decision |
| `pipeline.py` | The daily job: scrape, read CRM, match, save |
| `apply.py` | Writes one approved proposal to the CRM |
| `app.py`, `templates/index.html` | The review page |
| `tests/test_sync.py` | 31 tests with a fake CRM, no internet needed |

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # put your token in BH_TOKEN

python pipeline.py probe        # check your token and the field names first
python -m unittest discover tests
python pipeline.py run          # makes proposals, does not touch the CRM
python app.py                   # open http://127.0.0.1:5000
```

If you run `python pipeline.py run` a second time, it says `0 new`.

If the website shows fewer than 70% of the communities from the last run (or the CRM returns fewer than 70% of the accounts), the run stops without saving anything. That is usually a broken page, not mass closures. Check the site, then use `python pipeline.py run --force` if the drop is real.

The review app runs with Flask debug mode off. Set `BH_DEBUG=true` to turn it on while developing, and only on your own machine.

## The review page

- Proposals are grouped by type, with chips at the top to jump between groups.
- Each card shows what will change as old value → new value, the website and CRM side by side, and why it matched.
- Warnings (open AR on a duplicate, a PO box billing address, revenue on an account with no parent) are shown in red. Use "Only ones with warnings" to see just those.
- The filter box searches by name, city or account id. Press `/` to jump to it.
- Duplicates, CHOW, new accounts and "not on the website" ask you to confirm before they write.
- The note box text is added to the account's CRM note when you approve.
- A failed approve shows the error and a Retry button. A rejected item can be reopened from the Decided list.

## Schedule

`schedule/crontab.txt` has the cron line I would use. `.github/workflows/daily-sync.yml` does the same thing on GitHub Actions. It is set to manual for now (Actions tab → Run workflow); the file shows the two lines to add to make it daily, after you add `BH_TOKEN` as a repository secret. It runs the tests, runs the pipeline and saves the decision file back to the repo.

## Writeup

### How I match

I open every community page, not just the list. The list only shows city and state. The street and zip on each page tell me if two places with the same name are really the same building. They also catch a place that got renamed.

Before comparing, I clean up the text so small differences don't matter. "Rehab" and "Rehabilitation" count as the same. So do "&" and "and", "Centre" and "Center", and "Street" and "St". Small words like "the", "of" and "at" are ignored.

Then each account gets a score against each location:

1. A different state is never a match.
2. Same street plus same zip or city is a match, whatever the name says. That is how renames get caught.
3. Same city and a very close name is a match.
4. Same zip and a fairly close name is a match.

A name on its own is never enough. There is an Amberly Manor in Colorado under Juniper Point and an Amberly Manor in Hudson, Ohio on the Bellhaven site. They stay separate.

Each account goes to the one location it fits best. If two accounts land on the same location, they are duplicates.

### What it proposes

| What I found | What it suggests |
|---|---|
| Location has no account | Create one under Bellhaven with the website details |
| Two or more accounts for one place | Keep one and mark the others as duplicates (Inactive, with `duplicate_of_account` set) |
| Account sits under another company | Follow the CHOW rule below |
| Account has no parent | Set Bellhaven as the parent |
| Name is different from the website | Rename it to the website name |
| Street, zip or city is different | Update the address |
| Care type doesn't fit what the website offers | Update the care type |
| On the website but not Active | Set it Active |
| Under Bellhaven but not on the website | Set it to Needs Review and add a note |

To pick which duplicate to keep, I look at billing history first, then Active status, then whether it is already under Bellhaven, then an exact name match, then how many fields are filled in. Billing comes first because turning off the account that billing works from would cause the most trouble.

For accounts that are no longer on the website, I chose Needs Review over Inactive. I know Bellhaven stopped listing them. I don't know if they closed, got sold, or the website just missed them. Needs Review puts them in front of a person instead of hiding them.

### The CHOW rule

When an account sits under a different company:

1. If it has revenue and open AR, I leave the old account exactly as it is. I make a new account under Bellhaven and set `chow_current_account` on the old one to point at the new one.
2. If it has no revenue or no open AR, I just move it to Bellhaven.

If there is already a copy of that facility under Bellhaven, the old account points at that copy. That way it doesn't create a third account.

Once an account has `chow_current_account` or `duplicate_of_account` set, it counts as done and is left out of matching. That way the next day's run doesn't bring it up again.

One call I made: if an account has no parent at all, I set the parent directly and skip the CHOW check. Nothing moved from one owner to another; the link was just missing. If that account has revenue and open AR, the proposal shows you a warning. You can switch this with `CHOW_APPLIES_TO_UNPARENTED=true`.

### Running it again safely

1. Each proposal gets a key made from what it would change. If you already approved or rejected that key, it never shows up again. If the data changes, the key changes, so you see the new situation.
2. When you click Approve, it first checks the account again. If someone changed it since the proposal was made, it stops and asks you to rerun.
3. Before creating an account, it looks for one that already matches. Clicking Approve twice won't make two.
4. Every change adds a dated line to the account note. The CHOW accounts are the exception, because the rule says to leave them alone.
5. If an approve fails because the account changed, the next run refreshes that proposal with the new values, so Retry can work. If the fix is no longer needed, it is taken off the list.
6. Approve claims the proposal before writing, so a double click or two open tabs can't apply it twice. The buttons also turn off after one click.
7. A create is never retried automatically. If the CRM times out, the account might already exist, so the next Approve looks for it instead of making a second one.
8. If the website or the CRM suddenly returns far fewer records than last time, the run stops without saving anything (see above).
9. The review page only accepts forms it served itself, so another website can't post to it and approve something.

### Billing addresses

The CRM stores the billing address, and the website shows the building. They are usually the same, but a PO box is a normal billing address. When an address fix would replace a PO box, it is marked medium confidence with a warning to check with billing first.

### How I used AI tools

I used Claude, and I asked for it in small steps instead of handing over the whole brief at once. That way I could check each piece before moving on. These are the prompts, in order:

1. "Here is a take home brief. Before writing anything, read the website and the API docs and tell me what the data looks like and where the traps might be."
2. "The communities list is paginated. Write a scraper that walks every page, opens each community page and pulls name, street, city, state, zip and care offerings. Keep it simple."
3. "Write a small API client for the CRM with my token. It should page through all accounts and retry if the server hiccups."
4. "Now the matching. A name alone should never be a match. Use street and zip first, then city plus a close name. Show me the scoring before you go further."
5. "Add the rest of the checks: duplicates, wrong parent, missing parent, old names, address, care type, and Bellhaven accounts that are no longer on the website."
6. "Add the CHOW rule exactly as the brief says. If there is revenue and open AR, the old account stays untouched, a new one is made and the old one points to it. Otherwise just move it."
7. "Save every proposal in SQLite with a stable key so running it twice does not show the same thing again. Rejected items should stay rejected."
8. "Build a small Flask page that shows each proposal with the website and CRM values side by side. Approve writes to the CRM. Reject just saves the decision."
9. "Write tests with a fake CRM that has these traps: Lima under Harborview with AR, Tiffin with no AR, two Owosso accounts, Findlay with no parent, and the two Amberly Manors."
10. "Before it writes, check the account has not changed since the proposal. Also make sure approving a create twice does not make two accounts."
11. "Give me a cron line and a GitHub Actions file to run it daily."

After that I ran it against my own CRM copy and read each proposal next to the website and the CRM browser before approving it.

### What I would build next

1. Move the decision file to a shared database and record who approved what.
2. Send an alert (email or Slack) when the run stops because the website or CRM shrank, instead of only printing it.
3. Use contacts as extra evidence. If the administrator on the website matches a CRM contact, that makes the match stronger.
4. Look up map coordinates for each address, so matching still works when streets are written differently.
5. Handle facilities that split or merge as their own case, instead of sending them to Needs Review.
6. Add one click approval for the easy, high confidence fixes like renames.
