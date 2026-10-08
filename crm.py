"""Small client for the CRM sandbox API."""
import time

import requests

import config


class CRMError(RuntimeError):
    pass


def _items(payload):
    # the list endpoint could send a plain list or wrap it in an object
    if isinstance(payload, list):
        return payload
    for key in ("items", "results", "data", "accounts", "records"):
        if isinstance(payload.get(key), list):
            return payload[key]
    raise CRMError(f"Did not expect this response shape: {list(payload)[:10]}")


class CRM:
    def __init__(self, base=config.API_URL, token=config.TOKEN):
        if not token:
            raise CRMError("No API token. Put BH_TOKEN in your .env file")
        self.base = base
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _req(self, method, path, **kw):
        # Only retry calls that are safe to repeat. If a POST times out, the account may
        # already exist, so we stop and let the next Approve find it instead of making two.
        retry = method in ("GET", "PATCH")
        for attempt in range(3):
            last = attempt == 2 or not retry
            try:
                r = self.s.request(method, self.base + path, timeout=30, **kw)
            except requests.RequestException as e:
                if last:
                    raise CRMError(f"{method} {path} could not reach the CRM: {e}") from e
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (429, 502, 503, 504) and not last:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise CRMError(f"{method} {path} gave {r.status_code}: {r.text[:500]}")
            try:
                return r.json() if r.content else None
            except ValueError as e:
                raise CRMError(f"{method} {path} did not send JSON: {r.text[:200]}") from e

    def me(self):
        return self._req("GET", "/me")

    def list_accounts(self, **filters):
        out, seen, page, size = [], set(), 1, 50
        while True:
            batch = _items(self._req("GET", "/accounts", params={**filters, "page": page, "page_size": size}))
            fresh = [a for a in batch if a[config.F["id"]] not in seen]
            if not fresh:
                break
            out.extend(fresh)
            seen.update(a[config.F["id"]] for a in fresh)
            if len(batch) < size:
                break
            page += 1
        return out

    def get_account(self, account_id):
        return self._req("GET", f"/accounts/{account_id}")

    def create_account(self, fields):
        return self._req("POST", "/accounts", json=fields)

    def update_account(self, account_id, fields):
        return self._req("PATCH", f"/accounts/{account_id}", json=fields)
