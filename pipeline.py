"""The daily job. Scrape the site, read the CRM, match, save proposals.
It never writes to the CRM.

    python pipeline.py run          daily job
    python pipeline.py run --force  run even if the website or CRM shrank a lot since last time
    python pipeline.py probe    checks your token and shows one account so you can confirm field names
"""
import json
import sys
from datetime import datetime

import config
import match
import scrape
import store
from crm import CRM


# If today's numbers fall below this share of the last run, something is probably broken
# (a website redesign, a half-loaded page, an API hiccup), not 30 closures at once.
SHRINK_LIMIT = 0.7


class Suspicious(RuntimeError):
    pass


def sanity_check(db, locations, accounts):
    if not locations:
        raise Suspicious("The website gave 0 communities. Not saving anything.")
    last = store.last_run(db)
    if not last:
        return
    for what, today, before in (("website communities", len(locations), last["locations"]),
                                ("CRM accounts", len(accounts), last["accounts"])):
        if before and today < before * SHRINK_LIMIT:
            raise Suspicious(f"Only {today} {what} today, down from {before} last run. Not saving "
                             f"anything. Check the site, then run with --force if it is real.")


def run(crm=None, locations=None, db=None, snapshot=True, force=False):
    crm = crm or CRM()
    db = db or store.connect()
    locations = locations if locations is not None else scrape.scrape()
    accounts = crm.list_accounts()
    if not force:
        sanity_check(db, locations, accounts)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if snapshot:
        # keep what we saw today, in case you need to look back at it
        config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        (config.SNAPSHOT_DIR / f"{stamp}.json").write_text(
            json.dumps({"locations": locations, "accounts": accounts}, indent=1, default=str))

    proposals = match.build_proposals(locations, accounts)
    run_id, new = store.record_run(db, locations, accounts, proposals)
    counts = {}
    for p in proposals:
        counts[p["kind"]] = counts.get(p["kind"], 0) + 1
    print(f"run {run_id}: {len(locations)} website locations, {len(accounts)} CRM accounts")
    print(f"  {len(proposals)} proposals from today's data, {new} new (the rest were already decided or waiting)")
    for k, v in sorted(counts.items()):
        print(f"    {k:18} {v}")
    return run_id, new


def probe():
    crm = CRM()
    print("me:", json.dumps(crm.me(), indent=1))
    # print the raw response first, before we rely on any field names
    from crm import _items
    raw = crm._req("GET", "/accounts", params={"q": "bellhaven", "page": 1, "page_size": 3})
    print("\nRaw response keys:", list(raw) if isinstance(raw, dict) else "plain list")
    first = _items(raw)
    print("Raw accounts:")
    print(json.dumps(first, indent=1, default=str))
    if config.F["id"] not in first[0]:
        print(f"\nWARNING: no '{config.F['id']}' field. Fields are: {list(first[0])}")
        return
    accts = crm.list_accounts()
    print(f"{len(accts)} accounts. Here is the first one:")
    print(json.dumps(accts[0], indent=1))
    missing = [f for f in config.F.values() if f not in accts[0]]
    if missing:
        print("\nWARNING: these names in config.F were not in the API response:", missing)
        print("Fix them in config.py before you run.")
    parent = match.find_parent(accts)
    print("\nBellhaven parent:", parent.get(config.F["id"]), parent.get(config.F["name"]))


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = next((a for a in args if not a.startswith("-")), "run")
    try:
        if cmd == "run":
            run(force="--force" in args)
        else:
            {"probe": probe}[cmd]()
    except Suspicious as e:
        print("STOPPED:", e, file=sys.stderr)
        sys.exit(2)
