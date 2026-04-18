# SPDX-License-Identifier: GPL-3.0-or-later
"""
Homebox API client for fetching item details to supplement webhook template data.

Configure via environment variables:
  HOMEBOX_URL      — Base URL of your Homebox instance (e.g. http://192.168.1.10:7745)
  HOMEBOX_USER     — Login email/username
  HOMEBOX_PASSWORD — Password

When configured, the Homebox label endpoint enriches template variables with
full item data fetched from the API, making fields like {{location}}, {{serialNumber}},
{{manufacturer}}, and any custom field (e.g. {{Warranty Code}}) available in templates.
"""

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)


class HomeboxClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base = base_url.rstrip('/')
        self._username = username
        self._password = password
        self._token: str | None = None

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _login(self) -> None:
        payload = urllib.parse.urlencode({
            "username": self._username,
            "password": self._password,
        }).encode()
        req = urllib.request.Request(
            f"{self.base}/api/v1/users/login",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                raw = data.get("token", "")
                self._token = raw if raw.startswith("Bearer ") else f"Bearer {raw}"
        except Exception as exc:
            log.warning("Homebox login failed: %s", exc)
            self._token = None

    def _get(self, path: str):
        """Authenticated GET; re-authenticates once on 401. Returns parsed JSON or None."""
        for attempt in range(2):
            if not self._token:
                self._login()
            if not self._token:
                return None
            req = urllib.request.Request(
                f"{self.base}/api/v1{path}",
                headers={"Authorization": self._token},
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    self._token = None
                    continue
                log.warning("Homebox GET %s → HTTP %s", path, exc.code)
                return None
            except Exception as exc:
                log.warning("Homebox GET %s failed: %s", path, exc)
                return None
        return None

    # ------------------------------------------------------------------
    # Item lookup
    # ------------------------------------------------------------------

    def get_item_by_url(self, item_url: str) -> dict | None:
        """
        Resolve a Homebox item page URL to a full ItemOut dict.

        Handles two URL patterns:
          /i/{assetId}    — standard public URL (e.g. /i/000-001)
          /items/{uuid}   — direct UUID URL
        """
        path = urllib.parse.urlparse(item_url).path

        # Extract the identifier — try /i/{id} first, then /items/{id}
        m = re.search(r'/i/([^/?#]+)', path) or re.search(r'/items?/([^/?#]+)', path)
        if not m:
            log.warning("Cannot extract item identifier from URL: %s", item_url)
            return None

        identifier = m.group(1)

        # UUID → direct item lookup
        if _UUID_RE.match(identifier):
            return self._get(f"/items/{identifier}")

        # Asset ID → search with '#' prefix (Homebox v0.25 syntax)
        encoded = urllib.parse.quote(f"#{identifier}")
        result = self._get(f"/items?q={encoded}&pageSize=1")
        if result:
            items = result.get("items", [])
            if items:
                return self._get(f"/items/{items[0]['id']}")

        log.warning("No Homebox item found for identifier: %s", identifier)
        return None

    # ------------------------------------------------------------------
    # Variable flattening
    # ------------------------------------------------------------------

    def flatten_item(self, item: dict) -> dict:
        """
        Flatten a Homebox ItemOut dict into a flat string→string vars dict
        ready for template substitution.

        Available keys (use as {{key}} in templates):
          name, description, assetId, serialNumber, modelNumber, manufacturer,
          notes, purchaseFrom, purchasePrice, soldPrice, quantity,
          location, tags, <any custom field name>
        """
        vars_dict: dict[str, str] = {}

        for key in ("name", "description", "assetId", "serialNumber", "modelNumber",
                    "manufacturer", "notes", "purchaseFrom", "warrantyDetails", "soldNotes"):
            val = item.get(key)
            if val is not None:
                vars_dict[key] = str(val)

        for key in ("quantity", "purchasePrice", "soldPrice"):
            val = item.get(key)
            if val is not None:
                vars_dict[key] = str(val)

        loc = item.get("location")
        if isinstance(loc, dict) and loc.get("name"):
            vars_dict["location"] = loc["name"]

        tags = item.get("tags") or []
        if tags:
            vars_dict["tags"] = ", ".join(t["name"] for t in tags if t.get("name"))

        for cf in item.get("fields") or []:
            name = (cf.get("name") or "").strip()
            if not name:
                continue
            ftype = cf.get("type", "text")
            if ftype == "boolean":
                val = "Yes" if cf.get("booleanValue") else "No"
            elif ftype == "number":
                val = str(cf.get("numberValue", ""))
            else:
                val = str(cf.get("textValue") or "")
            vars_dict[name] = val

        return vars_dict


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_client: HomeboxClient | None = None


def get_client() -> HomeboxClient | None:
    """Return a configured HomeboxClient, or None if env vars are not set."""
    global _client
    if _client is not None:
        return _client
    url  = os.environ.get("HOMEBOX_URL", "").strip()
    user = os.environ.get("HOMEBOX_USER", "").strip()
    pwd  = os.environ.get("HOMEBOX_PASSWORD", "").strip()
    if url and user and pwd:
        _client = HomeboxClient(url, user, pwd)
        log.info("Homebox API client configured for %s", url)
    return _client


def fetch_item_vars(item_url: str) -> dict:
    """
    Fetch item data from the Homebox API and return a flat vars dict.
    Returns an empty dict if Homebox is not configured or on any error.
    """
    client = get_client()
    if not client:
        return {}
    try:
        item = client.get_item_by_url(item_url)
        if not item:
            return {}
        return client.flatten_item(item)
    except Exception as exc:
        log.warning("fetch_item_vars failed: %s", exc)
        return {}
