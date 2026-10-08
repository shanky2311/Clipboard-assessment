"""Writes one approved proposal to the CRM. Only the review app calls this."""
from datetime import date

from config import F
from normalize import norm_street, norm_zip


class Stale(RuntimeError):
    """The account changed after the proposal was made."""


def _note(existing, text):
    line = f"[Bellhaven sync {date.today().isoformat()}] {text}"
    return f"{existing}\n{line}" if existing else line


def _check_expect(crm, p):
    acct = crm.get_account(p["account_id"])
    for field, want in p["expect"].items():
        have = acct.get(field)
        if (have or None) != (want or None):
            raise Stale(f"{field} is now {have!r} but the proposal expected {want!r}. Run the pipeline again.")
    return acct


def _find_existing(crm, fields):
    # if you approve the same create twice, don't make two accounts
    for a in crm.list_accounts(q=fields[F["name"]], state=fields[F["state"]]):
        if (a.get(F["parent_id"]) == fields[F["parent_id"]]
                and a.get(F["name"]) == fields[F["name"]]
                and norm_street(a.get(F["street"])) == norm_street(fields[F["street"]])
                and norm_zip(a.get(F["zip"])) == norm_zip(fields[F["zip"]])):
            return a
    return None


def _create(crm, fields):
    existing = _find_existing(crm, fields)
    if existing:
        return existing, False
    return crm.create_account(fields), True


def apply(crm, p, reviewer_note="", reviewer=""):
    kind = p["kind"]
    why = f"{p['summary']}."
    if reviewer:
        why += f" Approved by {reviewer}."
    if reviewer_note:
        why += f" Note: {reviewer_note}"

    if kind == "create_account":
        acct, created = _create(crm, p["extra"]["new_account"])
        return {"account_id": acct[F["id"]], "created": created}

    acct = _check_expect(crm, p)

    if kind == "chow":
        # The SOP says leave the old account as it is. The only thing we set is the link to the new one.
        if p["extra"].get("use_existing"):
            new, created = crm.get_account(p["extra"]["use_existing"]), False
        else:
            new, created = _create(crm, p["extra"]["new_account"])
        crm.update_account(acct[F["id"]], {F["chow_current"]: new[F["id"]]})
        return {"old_account_id": acct[F["id"]], "new_account_id": new[F["id"]], "created": created}

    changes = dict(p["changes"])
    changes[F["note"]] = _note(acct.get(F["note"]), why)
    updated = crm.update_account(acct[F["id"]], changes)
    return {"account_id": acct[F["id"]], "patched": changes, "response": updated}
