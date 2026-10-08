"""Get every Bellhaven community from the website.

The list lives at /communities?page=N. Each community has its own page with the
address and care offerings, so we open every one of those too.
"""
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import config

KNOWN_OFFERINGS = [
    "Short-Term Rehabilitation & Nursing", "Short-term rehab & nursing", "Skilled Nursing",
    "Assisted Living", "Memory Support", "Memory Care", "Independent Living",
]
SECTION_LABELS = {"address", "care offerings", "administrator", "phone", "← back to all communities"}
CITY_LINE = re.compile(r"^(?P<city>.+?),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$")


def _get(session, url):
    for attempt in range(3):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def community_links(session, base=config.BASE_URL, max_pages=50):
    # keep going page by page until a page has nothing new on it
    links, page = [], 1
    while page <= max_pages:
        html = _get(session, f"{base}/communities?page={page}")
        soup = BeautifulSoup(html, "html.parser")
        new = []
        for a in soup.find_all("a", href=True):
            path = urlparse(urljoin(base, a["href"])).path.rstrip("/")
            if re.fullmatch(r"/communities/[^/]+", path):
                url = base + path
                if url not in links and url not in new:
                    new.append(url)
        if not new:
            break
        links.extend(new)
        page += 1
    return links


def _lines(soup):
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    return [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]


def _section(lines, label):
    # the lines that come after a label, until the next label starts
    low = [l.lower().rstrip(":") for l in lines]
    if label not in low:
        return []
    out = []
    for line, l in zip(lines[low.index(label) + 1:], low[low.index(label) + 1:]):
        if l in SECTION_LABELS:
            break
        out.append(line)
    return out


def split_offerings(lines):
    items = []
    for line in lines:
        for part in re.split(r"\s*[,;•|]\s*|\n", line):
            if not part:
                continue
            # sometimes two offerings come stuck together like "Assisted LivingMemory Support"
            found = [k for k in KNOWN_OFFERINGS if k.lower() in part.lower()]
            if len(found) > 1:
                items.extend(found)
            else:
                items.append(part)
    seen, out = set(), []
    for i in items:
        if i.lower() not in seen:
            seen.add(i.lower())
            out.append(i)
    return out


def parse_detail(html, url):
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    name = h1.get_text(" ", strip=True) if h1 else ""
    lines = _lines(soup)

    addr = _section(lines, "address")
    street = city = state = zip_ = ""
    for i, line in enumerate(addr):
        m = CITY_LINE.match(line)
        if m:
            city, state, zip_ = m["city"], m["state"], m["zip"]
            street = ", ".join(addr[:i])
            break

    return {
        "slug": urlparse(url).path.rstrip("/").split("/")[-1],
        "url": url,
        "name": name,
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_,
        "care_offerings": split_offerings(_section(lines, "care offerings")),
        # lines like "formerly known as" help when you check a rename
        "page_notes": [l for l in lines if re.search(r"formerly|previously|now part of|acquired", l, re.I)],
    }


def scrape(base=config.BASE_URL):
    s = requests.Session()
    s.headers["User-Agent"] = "bellhaven-sync/1.0"
    locations = []
    for url in community_links(s, base):
        loc = parse_detail(_get(s, url), url)
        if not (loc["name"] and loc["city"] and loc["state"]):
            raise RuntimeError(f"Could not read {url}: {loc}. Maybe the page layout changed?")
        locations.append(loc)
    return locations


if __name__ == "__main__":
    import json
    print(json.dumps(scrape(), indent=2))
