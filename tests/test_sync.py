"""Tests that run without the internet: matching, the CHOW rule, running twice, and the scraper.

    python -m unittest discover tests
"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import apply as applier  # noqa: E402
import match  # noqa: E402
import pipeline  # noqa: E402
import scrape  # noqa: E402
import store  # noqa: E402
from config import F  # noqa: E402

BH, HARBOR, JUNIPER = "acc-bh", "acc-harbor", "acc-juniper"


def acct(id, name, city, state, street="", zip="", parent=BH, care="Assisted Living",
         status="Active", rev=0, ar=0, **kw):
    return {F["id"]: id, "name": name, "parent_id": parent, F["street"]: street, F["city"]: city,
            F["state"]: state, F["zip"]: zip, "care_type": care, "status": status, "note": "",
            "lifetime_revenue": rev, "outstanding_ar": ar, "duplicate_of_account": None,
            "chow_current_account": None, **kw}


def loc(slug, name, city, state, street, zip, care=("Assisted Living",)):
    return {"slug": slug, "url": f"https://x/communities/{slug}", "name": name, "street": street,
            "city": city, "state": state, "zip": zip, "care_offerings": list(care), "page_notes": []}


LOCATIONS = [
    loc("akron", "Bellhaven Terrace of Akron", "Akron", "OH", "10 Main St", "44301"),
    loc("lima-billed", "Bellhaven Crossings of Lima", "Lima", "OH", "5 Elm Ave", "45801"),
    loc("tiffin", "Bellhaven of Tiffin", "Tiffin", "OH", "9 Oak Rd", "44883",
        care=("Short-Term Rehabilitation & Nursing",)),
    loc("owosso", "Bellhaven of Owosso", "Owosso", "MI", "1 Lake Dr", "48867"),
    loc("grove", "Bellhaven Rehabilitation & Nursing of Grove City", "Grove City", "OH",
        "77 Park Blvd", "43123", care=("Short-Term Rehabilitation & Nursing",)),
    loc("amberly", "Amberly Manor", "Hudson", "OH", "3 Hill St", "44236"),
    loc("findlay", "Bellhaven Meadows of Findlay", "Findlay", "OH", "1800 N Blanchard St", "45840",
        care=("Assisted Living", "Memory Support")),
    loc("renamed", "Bellhaven of Kettering", "Kettering", "OH", "400 Forest Avenue", "45429"),
]

ACCOUNTS = [
    {F["id"]: BH, "name": "Bellhaven Senior Living (Parent Account)", "parent_id": None, F["city"]: "", F["state"]: ""},
    {F["id"]: HARBOR, "name": "Harborview Care Group (Parent Account)", "parent_id": None, F["city"]: "", F["state"]: ""},
    {F["id"]: JUNIPER, "name": "Juniper Point Healthcare (Parent Account)", "parent_id": None, F["city"]: "", F["state"]: ""},
    acct("a1", "Bellhaven Terrace of Akron", "Akron", "OH", "10 Main Street", "44301"),
    acct("a2", "Bellhaven Crossings of Lima", "Lima", "OH", "5 Elm Ave", "45801",
         parent=HARBOR, rev=120000, ar=4500),                                                    # CHOW
    acct("a3", "Bellhaven of Tiffin", "Tiffin", "OH", "9 Oak Rd", "44883", parent=HARBOR,
         care="Skilled Nursing", rev=80000, ar=0),
    acct("a4", "Bellhaven of Owosso", "Owosso", "MI", "1 Lake Dr", "48867", rev=5000, ar=10),
    acct("a5", "Bellhaven of Owosso", "Owosso", "MI", "", "48867"),                              # same place, entered twice
    acct("a6", "Bellhaven Rehab and Nursing of Grove City", "Grove City", "OH", "77 Park Blvd",
         "43123", care="Skilled Nursing"),
    acct("a7", "Amberly Manor", "Colorado Springs", "CO", "8 Peak Rd", "80903", parent=JUNIPER), # different facility
    acct("a8", "Bellhaven Meadows of Findlay", "Findlay", "OH", "1800 N Blanchard St", "45840",
         parent=None),
    acct("a9", "Bellhaven Care Center of Alliance", "Alliance", "OH", "2 Rd", "44601"),          # gone from site
    acct("a10", "Forest Hills Care", "Kettering", "OH", "400 Forest Ave", "45429",
         parent=None, status="Inactive"),                                                        # renamed, same address
]


class FakeCRM:
    def __init__(self, accounts):
        self.accounts = {a[F["id"]]: copy.deepcopy(a) for a in accounts}
        self.writes, self.n = [], 0

    def list_accounts(self, q="", state="", **_):
        return [copy.deepcopy(a) for a in self.accounts.values()
                if q.lower() in (a.get("name") or "").lower() and (not state or a.get(F["state"]) == state)]

    def get_account(self, aid):
        return copy.deepcopy(self.accounts[aid])

    def create_account(self, fields):
        self.n += 1
        new = {F["id"]: f"new{self.n}", "duplicate_of_account": None, "chow_current_account": None,
               "lifetime_revenue": 0, "outstanding_ar": 0, **fields}
        self.accounts[new[F["id"]]] = new
        self.writes.append(("POST", new[F["id"]], fields))
        return copy.deepcopy(new)

    def update_account(self, aid, fields):
        self.accounts[aid].update(fields)
        self.writes.append(("PATCH", aid, fields))
        return copy.deepcopy(self.accounts[aid])


def by_kind(props):
    out = {}
    for p in props:
        out.setdefault(p["kind"], []).append(p)
    return out


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.props = match.build_proposals(LOCATIONS, ACCOUNTS)
        self.k = by_kind(self.props)

    def for_account(self, aid):
        return [p for p in self.props if p["account_id"] == aid]

    def test_clean_match_needs_nothing(self):
        self.assertEqual(self.for_account("a1"), [])  # "Street" and "St" count as the same

    def test_chow_when_revenue_and_ar(self):
        ps = self.for_account("a2")
        self.assertEqual([p["kind"] for p in ps], ["chow"])  # nothing else touches the old account
        self.assertEqual(ps[0]["extra"]["new_account"]["parent_id"], BH)

    def test_direct_reparent_when_no_ar(self):
        kinds = [p["kind"] for p in self.for_account("a3")]
        self.assertIn("reparent", kinds)
        self.assertNotIn("chow", kinds)

    def test_duplicate_keeps_billed_copy(self):
        dup = self.k["mark_duplicate"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["account_id"], "a5")
        self.assertEqual(dup[0]["changes"], {"duplicate_of_account": "a4", "status": "Inactive"})

    def test_rename_outdated_name(self):
        r = [p for p in self.for_account("a6") if p["kind"] == "rename"]
        self.assertEqual(r[0]["changes"]["name"], "Bellhaven Rehabilitation & Nursing of Grove City")

    def test_same_name_other_state_is_not_a_match(self):
        self.assertEqual(self.for_account("a7"), [])
        created = [p["slug"] for p in self.k["create_account"]]
        self.assertEqual(created, ["amberly"])

    def test_missing_parent_set_directly(self):
        self.assertEqual([p["kind"] for p in self.for_account("a8")], ["set_parent"])

    def test_not_on_website(self):
        self.assertEqual([p["account_id"] for p in self.k["not_on_website"]], ["a9"])
        self.assertEqual(self.k["not_on_website"][0]["changes"], {"status": "Needs Review"})

    def test_renamed_facility_matched_by_address(self):
        kinds = sorted(p["kind"] for p in self.for_account("a10"))
        self.assertEqual(kinds, ["reactivate", "rename", "set_parent"])

    def test_care_offering_mapping(self):
        self.assertFalse([p for p in self.props if p["kind"] == "update_care_type"
                          and p["account_id"] in ("a3", "a6")])


class ChowWithExistingCopyTest(unittest.TestCase):
    def test_points_at_existing_bellhaven_copy(self):
        locs = [loc("marietta", "Bellhaven of Marietta", "Marietta", "OH", "4 River St", "45750")]
        accts = ACCOUNTS[:3] + [
            acct("m1", "Bellhaven of Marietta", "Marietta", "OH", "4 River St", "45750",
                 parent=HARBOR, rev=50000, ar=900),
            acct("m2", "Marietta Health Center", "Marietta", "OH", "4 River Street", "45750"),
        ]
        k = by_kind(match.build_proposals(locs, accts))
        self.assertNotIn("mark_duplicate", k)
        self.assertEqual(k["chow"][0]["changes"], {"chow_current_account": "m2"})
        self.assertEqual(k["rename"][0]["account_id"], "m2")

        crm, db = FakeCRM(accts), store.connect(":memory:")
        pipeline.run(crm, locs, db, snapshot=False)
        for r in store.by_status(db, "pending"):
            store.decide(db, r["key"], "applied", result=applier.apply(crm, r["payload"]))
        self.assertEqual(crm.accounts["m1"]["chow_current_account"], "m2")
        self.assertEqual(crm.accounts["m1"]["parent_id"], HARBOR)
        self.assertFalse([w for w in crm.writes if w[0] == "POST"])
        _, new = pipeline.run(crm, locs, db, snapshot=False)
        self.assertEqual(new, 0)


class EndToEndTests(unittest.TestCase):
    def test_approve_all_then_rerun_proposes_nothing_new(self):
        crm, db = FakeCRM(ACCOUNTS), store.connect(":memory:")
        _, new = pipeline.run(crm, LOCATIONS, db, snapshot=False)
        self.assertGreater(new, 0)

        # second run before any decision: nothing new
        _, new = pipeline.run(crm, LOCATIONS, db, snapshot=False)
        self.assertEqual(new, 0)

        # reject one, approve the rest
        pending = store.by_status(db, "pending")
        rejected = next(r for r in pending if r["kind"] == "update_care_type" or r["kind"] == "rename")
        store.decide(db, rejected["key"], "rejected")
        for r in store.by_status(db, "pending"):
            store.decide(db, r["key"], "applied", result=applier.apply(crm, r["payload"]))

        # old Lima account stays put, only the link is set, and the new one is under Bellhaven
        old = crm.accounts["a2"]
        self.assertEqual(old["parent_id"], HARBOR)
        self.assertTrue(old["chow_current_account"])
        self.assertEqual(crm.accounts[old["chow_current_account"]]["parent_id"], BH)
        self.assertEqual(old["note"], "")
        self.assertEqual(crm.accounts["a3"]["parent_id"], BH)
        self.assertIn("Bellhaven sync", crm.accounts["a3"]["note"])
        self.assertEqual(crm.accounts["a5"]["status"], "Inactive")

        writes = len(crm.writes)
        _, new = pipeline.run(crm, LOCATIONS, db, snapshot=False)
        self.assertEqual(new, 0, store.by_status(db, "pending"))
        self.assertEqual(len(crm.writes), writes)  # pipeline itself never writes

    def test_create_is_not_repeated(self):
        crm = FakeCRM(ACCOUNTS)
        p = next(p for p in match.build_proposals(LOCATIONS, ACCOUNTS) if p["kind"] == "create_account")
        first = applier.apply(crm, p)
        second = applier.apply(crm, p)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["account_id"], second["account_id"])

    def test_stale_proposal_refused(self):
        crm = FakeCRM(ACCOUNTS)
        p = next(p for p in match.build_proposals(LOCATIONS, ACCOUNTS) if p["kind"] == "reparent")
        crm.accounts[p["account_id"]]["parent_id"] = JUNIPER  # someone changed it meanwhile
        with self.assertRaises(applier.Stale):
            applier.apply(crm, p)


DETAIL_HTML = """<html><body><nav><a href="/">Home</a></nav>
<h1>Bellhaven Meadows of Findlay</h1>
<div class="field"><div class="label">Address</div><div>1800 N Blanchard St<br>Findlay, OH 45840</div></div>
<div class="field"><div class="label">Care Offerings</div><span>Assisted Living</span><span>Memory Support</span></div>
<div class="field"><div class="label">Administrator</div><div>Sam Pruitt</div></div>
<a href="/communities">← Back to all communities</a></body></html>"""


class ScraperTests(unittest.TestCase):
    def test_parse_detail(self):
        d = scrape.parse_detail(DETAIL_HTML, "https://x/communities/bellhaven-meadows-of-findlay")
        self.assertEqual((d["name"], d["street"], d["city"], d["state"], d["zip"]),
                         ("Bellhaven Meadows of Findlay", "1800 N Blanchard St", "Findlay", "OH", "45840"))
        self.assertEqual(d["care_offerings"], ["Assisted Living", "Memory Support"])

    def test_glued_offerings(self):
        self.assertEqual(scrape.split_offerings(["Assisted LivingMemory Support"]),
                         ["Assisted Living", "Memory Support"])


class FixTests(unittest.TestCase):
    def test_failed_proposal_is_refreshed_and_can_succeed(self):
        crm, db = FakeCRM(ACCOUNTS), store.connect(":memory:")
        pipeline.run(crm, LOCATIONS, db, snapshot=False)
        row = next(r for r in store.by_status(db, "pending") if r["kind"] == "rename" and r["account_id"] == "a6")
        crm.accounts["a6"]["name"] = "Someone Edited This"  # changed after the run
        with self.assertRaises(applier.Stale):
            applier.apply(crm, row["payload"])
        store.decide(db, row["key"], "failed", result={"error": "stale"})

        pipeline.run(crm, LOCATIONS, db, snapshot=False)  # same change, so same key
        fresh = store.get(db, row["key"])
        self.assertEqual(fresh["status"], "failed")
        self.assertEqual(fresh["payload"]["expect"]["name"], "Someone Edited This")
        applier.apply(crm, fresh["payload"])  # now it goes through
        self.assertEqual(crm.accounts["a6"]["name"], "Bellhaven Rehabilitation & Nursing of Grove City")

    def test_failed_proposal_superseded_when_no_longer_needed(self):
        crm, db = FakeCRM(ACCOUNTS), store.connect(":memory:")
        pipeline.run(crm, LOCATIONS, db, snapshot=False)
        row = next(r for r in store.by_status(db, "pending") if r["kind"] == "rename" and r["account_id"] == "a6")
        store.decide(db, row["key"], "failed", result={"error": "x"})
        crm.accounts["a6"]["name"] = "Bellhaven Rehabilitation & Nursing of Grove City"  # fixed by hand
        pipeline.run(crm, LOCATIONS, db, snapshot=False)
        self.assertEqual(store.get(db, row["key"])["status"], "superseded")

    def test_po_box_is_flagged(self):
        accts = ACCOUNTS[:3] + [acct("p1", "Bellhaven Terrace of Akron", "Akron", "OH", "PO Box 517", "44301")]
        p = next(p for p in match.build_proposals(LOCATIONS[:1], accts) if p["kind"] == "update_address")
        self.assertEqual(p["confidence"], "medium")
        self.assertIn("PO box", p["warnings"][0])

    def test_claim_only_once(self):
        db = store.connect(":memory:")
        pipeline.run(FakeCRM(ACCOUNTS), LOCATIONS, db, snapshot=False)
        key = store.by_status(db, "pending")[0]["key"]
        self.assertTrue(store.claim(db, key))
        self.assertFalse(store.claim(db, key))  # second click
        db.execute("UPDATE proposals SET decided_at='2000-01-01T00:00:00+00:00' WHERE key=?", (key,))
        self.assertTrue(store.claim(db, key))   # a crash long ago can be retried

    def test_reopen_rejected(self):
        db = store.connect(":memory:")
        pipeline.run(FakeCRM(ACCOUNTS), LOCATIONS, db, snapshot=False)
        key = store.by_status(db, "pending")[0]["key"]
        store.decide(db, key, "rejected")
        self.assertTrue(store.reopen(db, key))
        self.assertEqual(store.get(db, key)["status"], "pending")
        self.assertFalse(store.reopen(db, key))

    def test_shrinking_website_stops_the_run(self):
        crm, db = FakeCRM(ACCOUNTS), store.connect(":memory:")
        pipeline.run(crm, LOCATIONS, db, snapshot=False)
        before = len(store.by_status(db, "pending"))
        with self.assertRaises(pipeline.Suspicious):
            pipeline.run(crm, LOCATIONS[:2], db, snapshot=False)
        self.assertEqual(len(store.by_status(db, "pending")), before)  # nothing superseded
        with self.assertRaises(pipeline.Suspicious):
            pipeline.run(crm, [], db, snapshot=False, force=False)
        pipeline.run(crm, LOCATIONS[:2], db, snapshot=False, force=True)


class AppTests(unittest.TestCase):
    def setUp(self):
        import app as webapp
        self.webapp = webapp
        self.crm, self.conn = FakeCRM(ACCOUNTS), store.connect(":memory:")
        pipeline.run(self.crm, LOCATIONS, self.conn, snapshot=False)
        self._db, self._crm = webapp.db, webapp.crm
        webapp.db, webapp.crm = (lambda: self.conn), (lambda: self.crm)
        webapp.app.config["TESTING"] = True
        self.client = webapp.app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as s:
            self.token = s["csrf"]
            s["reviewer"] = "Tester"

    def tearDown(self):
        self.webapp.db, self.webapp.crm = self._db, self._crm

    def key(self, kind):
        return next(r["key"] for r in store.by_status(self.conn, "pending") if r["kind"] == kind)

    def test_page_renders(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Will change", r.data)

    def test_post_without_token_is_refused(self):
        key = self.key("rename")
        r = self.client.post(f"/decide/{key}", data={"action": "approve"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(store.get(self.conn, key)["status"], "pending")
        self.assertFalse(self.crm.writes)

    def test_approve_twice_writes_once(self):
        key = self.key("rename")
        for _ in range(2):
            self.client.post(f"/decide/{key}", data={"action": "approve", "csrf": self.token})
        self.assertEqual(store.get(self.conn, key)["status"], "applied")
        self.assertEqual(len(self.crm.writes), 1)

    def test_unexpected_error_marks_failed(self):
        key = self.key("rename")
        self.crm.update_account = lambda *a: 1 / 0
        self.client.post(f"/decide/{key}", data={"action": "approve", "csrf": self.token})
        row = store.get(self.conn, key)
        self.assertEqual(row["status"], "failed")
        self.assertIn("ZeroDivisionError", row["result"])
        self.assertIn(b"last try failed", self.client.get("/").data)

    def test_reject_then_reopen(self):
        key = self.key("rename")
        self.client.post(f"/decide/{key}", data={"action": "reject", "csrf": self.token})
        self.assertEqual(store.get(self.conn, key)["status"], "rejected")
        self.client.post(f"/reopen/{key}", data={"csrf": self.token})
        self.assertEqual(store.get(self.conn, key)["status"], "pending")
        self.assertFalse(self.crm.writes)


class FeatureTests(AppTests):
    """Reviewer names, bulk approve, CSV export. Reuses the AppTests setup."""

    def test_page_renders(self):
        pass  # covered in AppTests

    def test_decisions_need_a_name(self):
        with self.client.session_transaction() as s:
            s.pop("reviewer")
        key = self.key("rename")
        self.client.post(f"/decide/{key}", data={"action": "approve", "csrf": self.token})
        self.assertEqual(store.get(self.conn, key)["status"], "pending")
        self.assertFalse(self.crm.writes)
        self.client.post("/reviewer", data={"name": "  Sam   Lee ", "csrf": self.token})
        self.client.post(f"/decide/{key}", data={"action": "approve", "csrf": self.token})
        row = store.get(self.conn, key)
        self.assertEqual((row["status"], row["reviewer"]), ("applied", "Sam Lee"))
        self.assertIn("Approved by Sam Lee", self.crm.writes[0][2]["note"])

    def test_bulk_approves_only_safe_ones(self):
        renames = [r for r in store.by_status(self.conn, "pending") if r["kind"] == "rename"]
        easy = [r for r in renames if r["confidence"] == "high" and not r["payload"]["warnings"]]
        self.assertTrue(easy and len(easy) < len(renames))  # the fixtures have both kinds
        self.client.post("/bulk/rename", data={"csrf": self.token})
        for r in renames:
            want = "applied" if r in easy else "pending"
            self.assertEqual(store.get(self.conn, r["key"])["status"], want, r["summary"])
        self.assertEqual(len(self.crm.writes), len(easy))

    def test_bulk_refused_for_risky_kinds(self):
        r = self.client.post("/bulk/chow", data={"csrf": self.token})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(self.crm.writes)

    def test_csv_export(self):
        key = self.key("rename")
        self.client.post(f"/decide/{key}", data={"action": "reject", "note": "not yet", "csrf": self.token})
        r = self.client.get("/export.csv")
        self.assertEqual(r.mimetype, "text/csv")
        lines = r.data.decode().splitlines()
        self.assertTrue(lines[0].startswith("decided_at,status,reviewer"))
        self.assertIn("rejected,Tester,rename", lines[1])
        self.assertIn("not yet", lines[1])


class AlertTests(unittest.TestCase):
    def setUp(self):
        import config
        self.config, self._hook = config, config.ALERT_WEBHOOK
        self.sent = []
        self._post = pipeline.requests.post
        pipeline.requests.post = lambda url, json, timeout: self.sent.append((url, json["text"]))

    def tearDown(self):
        self.config.ALERT_WEBHOOK = self._hook
        pipeline.requests.post = self._post

    def test_stopped_run_sends_alert(self):
        self.config.ALERT_WEBHOOK = "https://hooks.example/x"
        crm, db = FakeCRM(ACCOUNTS), store.connect(":memory:")
        pipeline.run(crm, LOCATIONS, db, snapshot=False)
        with self.assertRaises(pipeline.Suspicious):
            pipeline.run(crm, LOCATIONS[:1], db, snapshot=False)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("run stopped", self.sent[0][1])

    def test_no_webhook_no_alert(self):
        self.config.ALERT_WEBHOOK = ""
        self.assertFalse(pipeline.alert("hi"))
        self.assertFalse(self.sent)


class MigrationTest(unittest.TestCase):
    def test_old_file_gets_reviewer_column(self):
        import os
        import sqlite3
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE proposals (key TEXT PRIMARY KEY, kind TEXT NOT NULL, summary TEXT NOT NULL,"
                    " confidence TEXT, account_id TEXT, slug TEXT, payload TEXT NOT NULL,"
                    " status TEXT NOT NULL DEFAULT 'pending', first_run INTEGER, last_seen_run INTEGER,"
                    " decided_at TEXT, reviewer_note TEXT, result TEXT)")
        old.execute("INSERT INTO proposals (key, kind, summary, payload) VALUES ('k', 'rename', 's', '{}')")
        old.commit()
        old.close()
        db = store.connect(path)
        store.decide(db, "k", "rejected", reviewer="Sam")
        self.assertEqual(store.get(db, "k")["reviewer"], "Sam")


class CRMRetryTests(unittest.TestCase):
    """The real CRM client, with the network swapped out."""
    def setUp(self):
        import crm as crm_mod
        import requests
        self.mod, self.requests = crm_mod, requests
        self._sleep = crm_mod.time.sleep
        crm_mod.time.sleep = lambda s: None
        self.client = crm_mod.CRM(base="http://fake", token="t")
        self.calls = []

    def tearDown(self):
        self.mod.time.sleep = self._sleep

    def respond(self, *outcomes):
        outcomes = list(outcomes)

        def request(method, url, **kw):
            self.calls.append(method)
            o = outcomes.pop(0)
            if isinstance(o, Exception):
                raise o
            r = self.requests.Response()
            r.status_code, r._content = o, b'{"ok": true}'
            return r
        self.client.s.request = request

    def test_get_retries_after_network_error_and_502(self):
        self.respond(self.requests.ConnectionError("down"), 502, 200)
        self.assertEqual(self.client.get_account("x"), {"ok": True})
        self.assertEqual(self.calls, ["GET", "GET", "GET"])

    def test_post_is_never_retried(self):
        self.respond(502, 200)
        with self.assertRaises(self.mod.CRMError):
            self.client.create_account({"name": "x"})
        self.assertEqual(self.calls, ["POST"])

    def test_post_timeout_becomes_crm_error(self):
        self.respond(self.requests.Timeout("slow"))
        with self.assertRaises(self.mod.CRMError):
            self.client.create_account({"name": "x"})
        self.assertEqual(self.calls, ["POST"])

    def test_gives_up_after_three_tries(self):
        self.respond(503, 503, 503)
        with self.assertRaises(self.mod.CRMError):
            self.client.update_account("x", {"name": "y"})
        self.assertEqual(self.calls, ["PATCH"] * 3)


if __name__ == "__main__":
    unittest.main()
