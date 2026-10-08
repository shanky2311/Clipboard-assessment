"""Clean up names and addresses so small spelling differences don't block a match."""
import re
from difflib import SequenceMatcher

from config import CARE_MAP

NAME_SUBS = {
    "rehab": "rehabilitation",
    "centre": "center",
    "ctr": "center",
    "healthcare": "health care",
    "hc": "health care",
    "snf": "skilled nursing",
    "mt": "mount",
    "ft": "fort",
}
NAME_STOP = {"the", "of", "at", "a", "and", "in", "llc", "inc"}

STREET_SUBS = {
    "street": "st", "avenue": "ave", "av": "ave", "road": "rd", "drive": "dr",
    "boulevard": "blvd", "lane": "ln", "court": "ct", "parkway": "pkwy",
    "highway": "hwy", "place": "pl", "circle": "cir", "terrace": "ter",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
}


def _tokens(text):
    text = (text or "").lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9 ]+", " ", text).split()


def norm_name(name):
    out = []
    for t in _tokens(name):
        t = NAME_SUBS.get(t, t)
        out.extend(w for w in t.split() if w not in NAME_STOP)
    return " ".join(out)


def name_similarity(a, b):
    """Between 0 and 1. Takes the better of two checks: letters in order, and shared words."""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    sa, sb = set(na.split()), set(nb.split())
    jac = len(sa & sb) / len(sa | sb)
    return round(max(seq, jac), 3)


def norm_street(street):
    toks = [STREET_SUBS.get(t, t) for t in _tokens(street)]
    for marker in ("suite", "ste", "unit", "apt"):
        if marker in toks:
            toks = toks[: toks.index(marker)]
    return " ".join(toks)


PO_BOX = re.compile(r"\b(p\.?\s*o\.?\s*box|post\s+office\s+box)\b", re.I)


def is_po_box(street):
    return bool(PO_BOX.search(street or ""))


def norm_zip(z):
    m = re.search(r"\d{5}", str(z or ""))
    return m.group(0) if m else ""


def norm_city(c):
    return " ".join(_tokens(c))


def norm_state(s):
    return (s or "").strip().upper()


def care_types(offerings):
    out = []
    for off in offerings:
        low = off.lower()
        for needle, crm in CARE_MAP:
            if needle in low:
                if crm not in out:
                    out.append(crm)
                break
    return out


def money(v):
    # the API might send 1200, "1200" or "$1,200.00"
    if v in (None, ""):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(v)) or 0)
    except ValueError:
        return 0.0
