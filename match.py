"""Match each website location to its CRM account and list what needs fixing.

No network calls and no database here. It takes the scraped locations and all CRM
accounts, and gives back a list of proposals for a person to look at.
"""
import hashlib
import json

import config
from config import F
from normalize import (care_types, is_po_box, money, name_similarity, norm_city,
                       norm_state, norm_street, norm_zip)

HIGH, MEDIUM = "high", "medium"


def g(acct, field):
    return acct.get(F[field])


def find_parent(accounts):
    if config.BELLHAVEN_PARENT_ID:
        return next(a for a in accounts if str(g(a, "id")) == config.BELLHAVEN_PARENT_ID)
    named = [a for a in accounts
             if config.BELLHAVEN_PARENT_NAME.lower() in (g(a, "name") or "").lower()
             and not g(a, "parent_id") and not g(a, "city")]
    if not named:
        raise RuntimeError("Could not find the Bellhaven parent account. Set BELLHAVEN_PARENT_ID")
    # if the parent account is in there twice, use the one with more facilities under it
    kids = lambda p: sum(1 for a in accounts if g(a, "parent_id") == g(p, "id"))
    return max(named, key=kids)


def is_retired(acct):
    return bool(g(acct, "duplicate_of") or g(acct, "chow_current"))


def proposal(kind, summary, *, account=None, location=None, changes=None, expect=None,
             evidence=None, confidence=HIGH, warnings=None, extra=None):
    # The key only depends on what would change, so tomorrow's run makes the same key
    # and we can tell it was already decided.
    changes = changes or {}
    ident = {"kind": kind, "account": account and g(account, "id"),
             "slug": location and location["slug"], "changes": changes, "extra": extra}
    key = hashlib.sha1(json.dumps(ident, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {
        "key": key,
        "kind": kind,
        "summary": summary,
        "confidence": confidence,
        "account_id": account and g(account, "id"),
        "slug": location and location["slug"],
        "changes": changes,
        # what the account should still look like when you approve; if not, we stop
        "expect": expect or {},
        "extra": extra or {},
        "evidence": {
            "website": location,
            "crm": account,
            **(evidence or {}),
        },
        "warnings": warnings or [],
    }


def score(loc, acct):
    """Returns (score, reasons). A score of 0 means it is not this facility."""
    if norm_state(g(acct, "state")) != norm_state(loc["state"]):
        return 0, []
    street = bool(loc["street"]) and norm_street(g(acct, "street")) == norm_street(loc["street"])
    zip_ = bool(loc["zip"]) and norm_zip(g(acct, "zip")) == norm_zip(loc["zip"])
    city = norm_city(g(acct, "city")) == norm_city(loc["city"])
    sim = name_similarity(g(acct, "name"), loc["name"])

    reasons = [f"name similarity {sim:.2f}"]
    reasons += ["street matches"] * street + ["zip matches"] * zip_ + ["city matches"] * city

    if street and (zip_ or city):
        return 100 + sim * 10, reasons  # same building, even if the name changed
    if city and sim >= 0.85:
        return 80 + sim * 10, reasons
    if zip_ and sim >= 0.6:
        return 70 + sim * 10, reasons
    return 0, reasons


def assign(locations, accounts):
    # every account goes to the one location it fits best
    # if a location ends up with two or more accounts, those are duplicates
    best = {}
    for acct in accounts:
        for loc in locations:
            s, why = score(loc, acct)
            if s and s > best.get(g(acct, "id"), (0,))[0]:
                best[g(acct, "id")] = (s, loc["slug"], why)
    by_loc = {loc["slug"]: [] for loc in locations}
    by_id = {g(a, "id"): a for a in accounts}
    for aid, (s, slug, why) in best.items():
        by_loc[slug].append((s, why, by_id[aid]))
    for slug in by_loc:
        by_loc[slug].sort(key=lambda t: -t[0])
    return by_loc


def survivor_rank(acct, loc, parent_id):
    # Billing history comes first. Turning off the account that billing uses
    # would be the worst mistake here.
    filled = sum(1 for f in ("street", "city", "state", "zip", "care_type") if g(acct, f))
    return (
        money(g(acct, "lifetime_revenue")) > 0 or money(g(acct, "outstanding_ar")) > 0,
        g(acct, "status") == "Active",
        g(acct, "parent_id") == parent_id,
        (g(acct, "name") or "").strip() == loc["name"].strip(),
        filled,
    )


def new_account_fields(loc, parent_id, note):
    cts = care_types(loc["care_offerings"])
    fields = {
        F["name"]: loc["name"], F["parent_id"]: parent_id, F["street"]: loc["street"],
        F["city"]: loc["city"], F["state"]: loc["state"], F["zip"]: loc["zip"],
        F["status"]: "Active", F["note"]: note,
    }
    if cts:
        fields[F["care_type"]] = cts[0]
    return fields


def check_parent(loc, acct, parent, by_id):
    pid = g(parent, "id")
    current = g(acct, "parent_id")
    if current == pid:
        return None
    old_parent = by_id.get(current)
    old_name = g(old_parent, "name") if old_parent else None
    rev, ar = money(g(acct, "lifetime_revenue")), money(g(acct, "outstanding_ar"))
    ev = {"lifetime_revenue": rev, "outstanding_ar": ar, "current_parent": old_name,
          "target_parent": g(parent, "name")}
    # No parent at all is missing data, not a change of owner. See config.py.
    sop_applies = bool(current) or config.CHOW_APPLIES_TO_UNPARENTED

    if sop_applies and rev > 0 and ar > 0:
        return proposal(
            "chow",
            f"CHOW: keep '{g(acct, 'name')}' under {old_name or 'no parent'} for billing, and "
            f"create a new account under {g(parent, 'name')}",
            account=acct, location=loc,
            changes={F["chow_current"]: "<new account id>"},
            expect={F["parent_id"]: current, F["chow_current"]: None},
            extra={"new_account": new_account_fields(
                loc, pid, f"Created by the Bellhaven sync. Change of ownership from account {g(acct, 'id')}.")},
            evidence={**ev, "rule": "has revenue AND open AR, so the old account stays as it is"},
        )

    warnings = []
    if not current and rev > 0 and ar > 0:
        warnings.append("This account has revenue and open AR but no parent. I am treating it as a "
                        "missing link, not a change of owner. Reject this if billing sees it differently.")
    rule = ("no revenue or no open AR, so move it directly" if current
            else "account had no parent, so set it directly")
    return proposal(
        "reparent" if current else "set_parent",
        f"Move '{g(acct, 'name')}' from {old_name or 'no parent'} to {g(parent, 'name')}",
        account=acct, location=loc,
        changes={F["parent_id"]: pid},
        expect={F["parent_id"]: current},
        evidence={**ev, "rule": rule},
        warnings=warnings,
    )


def check_fields(loc, acct):
    out = []
    name = (g(acct, "name") or "").strip()
    if name != loc["name"].strip():
        out.append(proposal(
            "rename", f"Rename '{name}' to '{loc['name']}' (name on the website)",
            account=acct, location=loc, changes={F["name"]: loc["name"]},
            expect={F["name"]: g(acct, "name")},
            confidence=MEDIUM if name_similarity(name, loc["name"]) < 0.85 else HIGH))

    addr = {}
    if loc["street"] and norm_street(g(acct, "street")) != norm_street(loc["street"]):
        addr[F["street"]] = loc["street"]
    if loc["zip"] and norm_zip(g(acct, "zip")) != norm_zip(loc["zip"]):
        addr[F["zip"]] = loc["zip"]
    if norm_city(g(acct, "city")) != norm_city(loc["city"]):
        addr[F["city"]] = loc["city"]
    if addr:
        # The CRM holds the billing address and the website shows the building. A PO box
        # is a normal place to send bills, so don't treat it as a mistake without a person looking.
        warnings, conf = [], HIGH
        if F["street"] in addr and is_po_box(g(acct, "street")):
            conf = MEDIUM
            warnings.append(f"The CRM street is a PO box ({g(acct, 'street')}). This is the billing address, "
                            "so it may be right on purpose. Check with billing before replacing it with the building's street.")
        out.append(proposal(
            "update_address", f"Update address on '{name}'", account=acct, location=loc,
            changes=addr, expect={k: acct.get(k) for k in addr}, confidence=conf, warnings=warnings))

    cts = care_types(loc["care_offerings"])
    if cts and g(acct, "care_type") not in cts:
        out.append(proposal(
            "update_care_type", f"Care type on '{name}': {g(acct, 'care_type')} to {cts[0]}",
            account=acct, location=loc, changes={F["care_type"]: cts[0]},
            expect={F["care_type"]: g(acct, "care_type")}, confidence=MEDIUM,
            evidence={"website_offerings": loc["care_offerings"], "mapped": cts}))

    if g(acct, "status") != "Active":
        out.append(proposal(
            "reactivate", f"'{name}' is {g(acct, 'status')} but it is on the website, so set it Active",
            account=acct, location=loc, changes={F["status"]: "Active"},
            expect={F["status"]: g(acct, "status")}))
    return out


def build_proposals(locations, accounts):
    parent = find_parent(accounts)
    pid = g(parent, "id")
    by_id = {g(a, "id"): a for a in accounts}
    parent_ids = {g(a, "parent_id") for a in accounts if g(a, "parent_id")}
    # skip parent companies, and accounts we already sorted out on an earlier day
    pool = [a for a in accounts if g(a, "id") not in parent_ids and not is_retired(a)]

    proposals, matched = [], set()
    for slug, cands in assign(locations, pool).items():
        loc = next(l for l in locations if l["slug"] == slug)
        if not cands:
            proposals.append(proposal(
                "create_account", f"New: '{loc['name']}' ({loc['city']}, {loc['state']}) is not in the CRM",
                location=loc, extra={"new_account": new_account_fields(
                    loc, pid, "Created by the Bellhaven sync from the Bellhaven website.")},
                evidence={"nearest_crm_names": nearest(loc, pool)}))
            continue

        accts = [a for _, _, a in cands]
        keep = max(accts, key=lambda a: survivor_rank(a, loc, pid))
        matched.update(g(a, "id") for a in accts)
        match_ev = {g(a, "id"): {"score": round(s, 1), "reasons": why} for s, why, a in cands}

        parent_prop = check_parent(loc, keep, parent, by_id)
        current = keep
        if parent_prop and parent_prop["kind"] == "chow":
            # If there is already a copy under Bellhaven, point the old account at it
            # instead of making a third account.
            under_bh = [a for a in accts if a is not keep and g(a, "parent_id") == pid]
            if under_bh:
                current = max(under_bh, key=lambda a: survivor_rank(a, loc, pid))
                parent_prop = proposal(
                    "chow",
                    f"CHOW: keep '{g(keep, 'name')}' under its old parent for billing, and point it at "
                    f"the Bellhaven account that already exists ({g(current, 'id')})",
                    account=keep, location=loc,
                    changes={F["chow_current"]: g(current, "id")},
                    expect=parent_prop["expect"],
                    extra={"use_existing": g(current, "id")},
                    evidence={**{k: v for k, v in parent_prop["evidence"].items() if k not in ("website", "crm")},
                              "existing_bellhaven_copy": current})

        for dup in accts:
            if dup is keep or dup is current:
                continue
            survivor = current
            warn = []
            if money(g(dup, "outstanding_ar")) > 0:
                warn.append(f"This duplicate still has open AR of {money(g(dup, 'outstanding_ar')):,.2f}. Let billing know.")
            proposals.append(proposal(
                "mark_duplicate",
                f"'{g(dup, 'name')}' ({g(dup, 'id')}) is a duplicate of {g(survivor, 'id')}, so mark it Inactive",
                account=dup, location=loc,
                changes={F["duplicate_of"]: g(survivor, "id"), F["status"]: "Inactive"},
                expect={F["duplicate_of"]: None},
                evidence={"survivor": survivor, "match": match_ev,
                          "why_survivor": "kept by: billing history, then Active, then right parent, then exact name, then most fields filled"},
                warnings=warn))

        if parent_prop:
            parent_prop["evidence"]["match"] = match_ev
            proposals.append(parent_prop)
        # The SOP says leave a CHOW account exactly as it is, so no field fixes on it.
        if current is not keep or not (parent_prop and parent_prop["kind"] == "chow"):
            for p in check_fields(loc, current):
                p["evidence"]["match"] = match_ev
                proposals.append(p)

    for a in pool:
        if g(a, "parent_id") == pid and g(a, "id") not in matched and g(a, "status") == "Active":
            proposals.append(proposal(
                "not_on_website",
                f"'{g(a, 'name')}' ({g(a, 'city')}, {g(a, 'state')}) is under Bellhaven but not on the website",
                account=a, changes={F["status"]: "Needs Review"},
                expect={F["status"]: g(a, "status"), F["parent_id"]: pid},
                confidence=MEDIUM,
                evidence={"nearest_website_locations": nearest_locations(a, locations)}))
    return proposals


def nearest(loc, accounts, n=3):
    ranked = sorted(accounts, key=lambda a: -name_similarity(g(a, "name"), loc["name"]))[:n]
    return [{"id": g(a, "id"), "name": g(a, "name"), "city": g(a, "city"), "state": g(a, "state"),
             "similarity": name_similarity(g(a, "name"), loc["name"])} for a in ranked]


def nearest_locations(acct, locations, n=3):
    ranked = sorted(locations, key=lambda l: -name_similarity(g(acct, "name"), l["name"]))[:n]
    return [{"name": l["name"], "city": l["city"], "state": l["state"],
             "similarity": name_similarity(g(acct, "name"), l["name"])} for l in ranked]
