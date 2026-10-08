"""Review app. Run python app.py and open http://127.0.0.1:5000

Approve writes the change to the CRM. Reject only saves your decision.
Nothing else in this project writes to the CRM.
"""
import csv
import hmac
import io
import json
import os
import secrets

from flask import Flask, Response, abort, flash, redirect, render_template, request, session, url_for

import apply as applier
import config
import pipeline
import store
from crm import CRM, CRMError

app = Flask(__name__)
# A fresh key each start is fine here: it only protects the flash messages and the form token.
app.secret_key = os.environ.get("BH_SECRET_KEY") or secrets.token_hex(32)

KIND_ORDER = ["mark_duplicate", "chow", "reparent", "set_parent", "create_account", "rename",
              "update_address", "update_care_type", "reactivate", "not_on_website"]
KIND_LABEL = {
    "mark_duplicate": "Duplicates", "chow": "Change of ownership (CHOW rule)",
    "reparent": "Wrong parent", "set_parent": "Missing parent", "create_account": "Not in the CRM yet",
    "rename": "Old name", "update_address": "Address fixes", "update_care_type": "Care type",
    "reactivate": "On the website but not Active", "not_on_website": "Under Bellhaven but not on the website",
}
# These are hard to undo by hand, so the page asks once more before approving.
CONFIRM_KINDS = {"chow", "create_account", "mark_duplicate", "not_on_website"}
# Simple, reversible fixes that can be approved together. Only high confidence, no warnings.
BULK_KINDS = {"rename"}


def db():
    return store.connect()


def crm():
    return CRM()


@app.template_filter("pretty")
def pretty(v):
    return json.dumps(v, indent=1, default=str) if isinstance(v, (dict, list)) else ("" if v in (None, "") else v)


@app.template_filter("fromjson")
def fromjson(v):
    try:
        return json.loads(v) if v else {}
    except ValueError:
        return {"error": v}


@app.context_processor
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return {"csrf": session["csrf"], "KIND_LABEL": KIND_LABEL, "CONFIRM_KINDS": CONFIRM_KINDS,
            "BULK_KINDS": BULK_KINDS, "F": config.F, "reviewer": session.get("reviewer", "")}


@app.before_request
def check_csrf():
    # Any web page you visit can post a form to 127.0.0.1. This token makes sure the
    # click came from this page.
    if request.method == "POST":
        sent, want = request.form.get("csrf", ""), session.get("csrf", "")
        if not want or not hmac.compare_digest(sent, want):
            abort(400, "This form is out of date. Reload the page and try again.")


@app.route("/")
def index():
    conn = db()
    pending = store.by_status(conn, "pending", "failed", "applying")
    groups = [(k, KIND_LABEL.get(k, k), [p for p in pending if p["kind"] == k]) for k in KIND_ORDER]
    groups += [(k, k, [p for p in pending if p["kind"] == k])
               for k in sorted({p["kind"] for p in pending} - set(KIND_ORDER))]
    groups = [g for g in groups if g[2]]
    done = store.by_status(conn, "applied", "rejected", "superseded")
    done.sort(key=lambda r: r["decided_at"] or "", reverse=True)
    n_failed = sum(1 for p in pending if p["status"] == "failed")
    return render_template("index.html", groups=groups, done=done, run=store.last_run(conn),
                           n_pending=len(pending), n_failed=n_failed)


def reviewer():
    return session.get("reviewer", "")


def need_reviewer():
    if reviewer():
        return None
    flash("Enter your name at the top first, so every decision says who made it.", "warn")
    return redirect(url_for("index"))


def approve_one(conn, p, note=""):
    """Claim, write to the CRM, record the result. Returns (ok, message)."""
    key = p["key"]
    if not store.claim(conn, key):
        return False, "This one was already decided, or is being applied right now."
    try:
        result = applier.apply(crm(), p["payload"], note, reviewer())
    except (CRMError, applier.Stale) as e:
        store.decide(conn, key, "failed", note, {"error": str(e)}, reviewer())
        return False, f"Failed: {e}"
    except Exception as e:  # noqa: BLE001 - never leave a row stuck in "applying"
        store.decide(conn, key, "failed", note, {"error": f"{type(e).__name__}: {e}"}, reviewer())
        return False, f"Failed with an unexpected error: {e}"
    store.decide(conn, key, "applied", note, result, reviewer())
    return True, f"Done: {p['summary']}"


@app.post("/reviewer")
def set_reviewer():
    name = " ".join(request.form.get("name", "").split())[:60]
    if name:
        session["reviewer"] = name
        flash(f"Reviewing as {name}.", "ok")
    else:
        session.pop("reviewer", None)
    return redirect(url_for("index"))


@app.post("/decide/<key>")
def decide(key):
    if (stop := need_reviewer()):
        return stop
    conn = db()
    p = store.get(conn, key)
    if not p:
        abort(404)
    note = request.form.get("note", "").strip()
    action = request.form.get("action")

    if action == "reject":
        if p["status"] not in store.OPEN:
            flash("This one was already decided.", "warn")
        else:
            store.decide(conn, key, "rejected", note, reviewer=reviewer())
            flash(f"Rejected: {p['summary']}", "ok")
        return redirect(url_for("index") + "#decided")

    if action != "approve":
        abort(400, "Unknown action")
    ok, msg = approve_one(conn, p, note)
    flash(msg, "ok" if ok else "err")
    return redirect(url_for("index") + ("" if ok else f"#k-{key}"))


@app.post("/bulk/<kind>")
def bulk(kind):
    """Approve every pending high-confidence proposal of one safe kind. Each one still goes
    through the same claim and stale checks, so one failure doesn't stop the rest."""
    if kind not in BULK_KINDS:
        abort(400, "Bulk approve is not allowed for this kind")
    if (stop := need_reviewer()):
        return stop
    conn = db()
    todo = [r for r in store.by_status(conn, "pending")
            if r["kind"] == kind and r["confidence"] == "high" and not r["payload"]["warnings"]]
    done, failed = 0, []
    for r in todo:
        ok, msg = approve_one(conn, r, "Bulk approved")
        if ok:
            done += 1
        else:
            failed.append(msg)
    flash(f"Bulk approved {done} of {len(todo)}.", "ok" if not failed else "warn")
    for msg in failed:
        flash(msg, "err")
    return redirect(url_for("index"))


@app.get("/export.csv")
def export():
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=store.AUDIT_COLUMNS)
    w.writeheader()
    w.writerows(store.audit_rows(db()))
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=bellhaven-decisions.csv"})


@app.post("/reopen/<key>")
def reopen(key):
    if store.reopen(db(), key):
        flash("Back on the list.", "ok")
    else:
        flash("Only rejected items can be reopened.", "warn")
    return redirect(url_for("index"))


@app.post("/run")
def run_pipeline():
    try:
        run_id, new = pipeline.run(force=request.form.get("force") == "1")
        flash(f"Pipeline run {run_id}: {new} new proposals.", "ok")
    except pipeline.Suspicious as e:
        flash(f"Stopped: {e}", "err")
    except Exception as e:  # noqa: BLE001
        flash(f"Pipeline failed: {e}", "err")
    return redirect(url_for("index"))


if __name__ == "__main__":
    # Debug mode lets anyone who can reach the page run code, so it is opt-in.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("BH_DEBUG", "").lower() == "true")
