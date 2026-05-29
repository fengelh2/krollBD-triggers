"""Hunter.io helper — verified named-RO email finder.

Used by `publish_triggers_to_github.py` at trigger time. Cap is 50 lookups/mo
on the free tier, so this module:
  - Caches every lookup on disk so re-runs are free
  - Tracks remaining quota (best-effort, via /account endpoint)
  - Refuses to call if remaining quota < HUNTER_FLOOR
  - Returns a structured record the publisher can fold into email_candidates()

Quota policy lives here, not in the publisher. Publisher just calls
`find_email(domain, first, last)` and trusts the return.
"""

from __future__ import annotations

import json
import os
import datetime as dt
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # repo root
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.environ.get("HUNTER_API_KEY")
CACHE_PATH = PROJECT_ROOT / "data" / "hunter_io_cache.json"
QUOTA_FLOOR = 5  # never burn the last 5 lookups — reserved for ad-hoc + retries
TIMEOUT = 15


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def _cache_key(domain: str, first: str, last: str) -> str:
    return f"{(domain or '').lower().strip()}|{(first or '').lower().strip()}|{(last or '').lower().strip()}"


def remaining_quota() -> int:
    """Best-effort — Hunter's /account endpoint returns the current monthly
    usage. Returns -1 if API is unreachable or key missing (callers should
    treat -1 as 'unknown, skip'). Free tier defaults to 50/mo."""
    if not API_KEY:
        return -1
    try:
        r = requests.get(
            "https://api.hunter.io/v2/account",
            params={"api_key": API_KEY},
            timeout=TIMEOUT,
        )
        if not r.ok:
            return -1
        data = r.json().get("data", {})
        # Two possible shapes: requests.available - requests.used, or
        # requests.searches.available - requests.searches.used. Try both.
        rq = data.get("requests", {})
        if isinstance(rq, dict):
            if "searches" in rq:
                s = rq["searches"] or {}
                return max(0, int(s.get("available", 0)) - int(s.get("used", 0)))
            if "available" in rq and "used" in rq:
                return max(0, int(rq["available"]) - int(rq["used"]))
        return -1
    except Exception:
        return -1


def find_email(domain: str, first: str, last: str) -> dict | None:
    """Return a dict like:
      {
        "email": "kmcgann@hamiltonlane.com",
        "score": 92,
        "confidence": "high",   # high|medium|low (derived from score)
        "verification_status": "valid",  # Hunter's own field
        "source": "hunter.io",
        "fetched_at_utc": "...",
        "quota_remaining_when_fetched": 47,
      }
    Returns None if no result, or if quota too low, or domain/name empty.

    Callers MUST treat None as "skip Hunter for this RO this run."
    """
    if not (API_KEY and domain and first and last):
        return None
    key = _cache_key(domain, first, last)
    cache = _load_cache()
    if key in cache:
        return cache[key]

    qr = remaining_quota()
    if qr != -1 and qr < QUOTA_FLOOR:
        # don't burn the floor
        return None

    try:
        r = requests.get(
            "https://api.hunter.io/v2/email-finder",
            params={
                "domain": domain,
                "first_name": first,
                "last_name": last,
                "api_key": API_KEY,
            },
            timeout=TIMEOUT,
        )
    except Exception:
        return None

    if r.status_code == 402:  # quota exhausted
        return None
    if not r.ok:
        return None

    data = (r.json() or {}).get("data") or {}
    email = data.get("email")
    if not email:
        # cache the miss too so we don't re-query
        miss = {
            "email": None,
            "score": None,
            "confidence": "none",
            "verification_status": data.get("verification", {}).get("status") or "no_match",
            "source": "hunter.io",
            "fetched_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "quota_remaining_when_fetched": qr,
        }
        cache[key] = miss
        _save_cache(cache)
        return miss

    score = int(data.get("score") or 0)
    conf = "high" if score >= 85 else "medium" if score >= 60 else "low"
    rec = {
        "email": email.lower().strip(),
        "score": score,
        "confidence": conf,
        "verification_status": (data.get("verification") or {}).get("status") or "unknown",
        "source": "hunter.io",
        "fetched_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "quota_remaining_when_fetched": qr,
    }
    cache[key] = rec
    _save_cache(cache)
    return rec


def main():
    """Quick CLI for debugging: `python hunter_io.py <domain> <first> <last>`"""
    import sys
    if len(sys.argv) < 4:
        print(f"usage: {sys.argv[0]} <domain> <first> <last>")
        print(f"quota remaining: {remaining_quota()}")
        sys.exit(1)
    res = find_email(sys.argv[1], sys.argv[2], sys.argv[3])
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
