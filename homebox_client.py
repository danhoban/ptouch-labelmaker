# SPDX-License-Identifier: GPL-3.0-or-later
"""
Homebox API client for fetching item details to supplement webhook template data.

Configure via environment variables:
  HOMEBOX_URL     — Base URL of your Homebox instance (e.g. http://192.168.1.10:7745)
  HOMEBOX_API_KEY — API key generated from your Homebox profile page (hb_... prefix)

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
    def __init__(self, base_url: str, api_key: str):
        self.base = base_url.rstrip('/')
        self._token = api_key if api_key.startswith("Bearer ") else f"Bearer {api_key}"

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, path: str):
        """Authenticated GET. Returns parsed JSON or None."""
        req = urllib.request.Request(
            f"{self.base}/api/v1{path}",
            headers={"Authorization": self._token},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            log.warning("Homebox GET %s → HTTP %s", path, exc.code)
            return None
        except Exception as exc:
            log.warning("Homebox GET %s failed: %s", path, exc)
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
        m = re.search(r'/[ia]/([^/?#]+)', path) or re.search(r'/items?/([^/?#]+)', path)
        if not m:
            log.warning("Cannot extract item identifier from URL: %s", item_url)
            return None

        identifier = m.group(1)

        # UUID → direct entity lookup
        if _UUID_RE.match(identifier):
            return self._get(f"/entities/{identifier}")

        # Asset ID → search with '#' prefix
        encoded = urllib.parse.quote(f"#{identifier}")
        result = self._get(f"/entities?q={encoded}&pageSize=1")
        if result:
            items = result.get("items", [])
            if items:
                return self._get(f"/entities/{items[0]['id']}")

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
          warrantyExpires, purchaseDate, soldDate,
          location, tags, collection (from Label-* tag),
          <any custom field name>
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

        # Date fields — skip Go zero-value sentinel dates (year "0001")
        for key in ("warrantyExpires", "purchaseDate", "soldDate"):
            val = (item.get(key) or "").strip()
            if val and not val.startswith("0001-"):
                vars_dict[key] = val[:10]  # keep YYYY-MM-DD only

        loc = item.get("location")
        if isinstance(loc, dict) and loc.get("name"):
            vars_dict["location"] = loc["name"]

        tags = item.get("tags") or []
        if tags:
            vars_dict["tags"] = ", ".join(t["name"] for t in tags if t.get("name"))
            for t in tags:
                name = t.get("name", "")
                if name.startswith("Label-") and len(name) > 6:
                    vars_dict["collection"] = name[6:]
                    break

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
    url     = os.environ.get("HOMEBOX_URL", "").strip()
    api_key = os.environ.get("HOMEBOX_API_KEY", "").strip()
    if url and api_key:
        _client = HomeboxClient(url, api_key)
        log.info("Homebox API client configured for %s", url)
    else:
        missing = [k for k, v in [("HOMEBOX_URL", url), ("HOMEBOX_API_KEY", api_key)] if not v]
        log.info("Homebox API not configured (missing: %s)", ", ".join(missing))
    return _client


def fetch_item_vars(item_url: str) -> dict:
    """
    Fetch item data from the Homebox API and return a flat vars dict.
    Returns an empty dict if Homebox is not configured or on any error.
    """
    client = get_client()
    if not client:
        return {}
    log.info("Fetching Homebox item data for %s", item_url)
    try:
        item = client.get_item_by_url(item_url)
        if not item:
            return {}
        vars_dict = client.flatten_item(item)
        log.info("Homebox item fetched: name=%r collection=%r", vars_dict.get("name"), vars_dict.get("collection"))
        return vars_dict
    except Exception as exc:
        log.warning("fetch_item_vars failed: %s", exc)
        return {}
