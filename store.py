"""A small SQLite file that remembers every proposal and what you decided.
This is why running the pipeline twice does not show you the same thing again."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    key          TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    summary      TEXT NOT NULL,
    confidence   TEXT,
    account_id   TEXT,
    slug         TEXT,
    payload      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending, applying, applied, rejected, failed, superseded
    first_run    INTEGER,
    last_seen_run INTEGER,
    decided_at   TEXT,
    reviewer_note TEXT,
    result       TEXT,
    reviewer     TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, locations INTEGER, accounts INTEGER, proposed INTEGER, new INTEGER
);
"""


# Things you can still act on. "failed" can be retried with Approve.
OPEN = ("pending", "failed")
# If the app died halfway through an approve, let it be retried after this long.
APPLYING_TIMEOUT = timedelta(minutes=2)


def now(delta=timedelta(0)):
    return (datetime.now(timezone.utc) + delta).isoformat(timespec="seconds")


def connect(path=config.DB_PATH):
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # the cron job and the review app can touch the file at the same time
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    # files made before the reviewer column existed
    cols = {r[1] for r in db.execute("PRAGMA table_info(proposals)")}
    if "reviewer" not in cols:
        db.execute("ALTER TABLE proposals ADD COLUMN reviewer TEXT")
        db.commit()
    return db


def record_run(db, locations, accounts, proposals):
    cur = db.execute("INSERT INTO runs (started_at, locations, accounts, proposed) VALUES (?,?,?,?)",
                     (now(), len(locations), len(accounts), len(proposals)))
    run_id = cur.lastrowid
    new = 0
    for p in proposals:
        exists = db.execute("SELECT 1 FROM proposals WHERE key=?", (p["key"],)).fetchone()
        if exists:
            # Seen before: leave the decision alone. If it is still open, refresh it with
            # today's data. That matters for "failed": a stale proposal can only succeed
            # once its expected values are brought up to date.
            db.execute("UPDATE proposals SET last_seen_run=?, "
                       "payload=CASE WHEN status IN ('pending','failed') THEN ? ELSE payload END, "
                       "summary=CASE WHEN status IN ('pending','failed') THEN ? ELSE summary END, "
                       "confidence=CASE WHEN status IN ('pending','failed') THEN ? ELSE confidence END "
                       "WHERE key=?",
                       (run_id, json.dumps(p, default=str), p["summary"], p["confidence"], p["key"]))
        else:
            new += 1
            db.execute("INSERT INTO proposals (key, kind, summary, confidence, account_id, slug, payload,"
                       " first_run, last_seen_run) VALUES (?,?,?,?,?,?,?,?,?)",
                       (p["key"], p["kind"], p["summary"], p["confidence"],
                        p["account_id"] and str(p["account_id"]), p["slug"],
                        json.dumps(p, default=str), run_id, run_id))
    # If something was open but today's run did not make it again, the data changed
    # under it. Take it off the list so nobody approves an old idea.
    db.execute("UPDATE proposals SET status='superseded', decided_at=? "
               "WHERE status IN ('pending','failed') AND last_seen_run < ?", (now(), run_id))
    db.execute("UPDATE runs SET new=? WHERE id=?", (new, run_id))
    db.commit()
    return run_id, new


def get(db, key):
    row = db.execute("SELECT * FROM proposals WHERE key=?", (key,)).fetchone()
    return dict(row, payload=json.loads(row["payload"])) if row else None


def by_status(db, *statuses):
    q = f"SELECT * FROM proposals WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY kind, summary"
    return [dict(r, payload=json.loads(r["payload"])) for r in db.execute(q, statuses)]


def claim(db, key):
    """Mark a proposal as being applied. Returns False if someone else got there first,
    so a double click or two open tabs can't write the same change twice."""
    cur = db.execute("UPDATE proposals SET status='applying', decided_at=? WHERE key=? AND "
                     "(status IN ('pending','failed') OR (status='applying' AND decided_at < ?))",
                     (now(), key, now(-APPLYING_TIMEOUT)))
    db.commit()
    return cur.rowcount == 1


def reopen(db, key):
    """Put a rejected proposal back on the list. The next run supersedes it if the data moved on."""
    cur = db.execute("UPDATE proposals SET status='pending', decided_at=NULL, reviewer_note=NULL, "
                     "result=NULL, reviewer=NULL WHERE key=? AND status='rejected'", (key,))
    db.commit()
    return cur.rowcount == 1


def decide(db, key, status, note="", result=None, reviewer=None):
    db.execute("UPDATE proposals SET status=?, decided_at=?, reviewer_note=?, result=?, reviewer=? WHERE key=?",
               (status, now(), note, json.dumps(result, default=str) if result else None, reviewer, key))
    db.commit()


AUDIT_COLUMNS = ["decided_at", "status", "reviewer", "kind", "account_id", "slug", "summary",
                 "reviewer_note", "result", "first_run", "key"]


def audit_rows(db):
    """Every decision, newest first, for the CSV export."""
    q = (f"SELECT {', '.join(AUDIT_COLUMNS)} FROM proposals "
         "WHERE status IN ('applied','rejected','failed','superseded') ORDER BY decided_at DESC")
    rows = []
    for r in db.execute(q):
        row = dict(r)
        if row["result"]:
            # keep ids and errors, drop the full CRM response
            res = json.loads(row["result"])
            row["result"] = json.dumps({k: v for k, v in res.items() if k not in ("response", "patched")})
        rows.append(row)
    return rows


def last_run(db):
    row = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None
