"""Apollo.io helper — find the DECISION-MAKER at a firm (not the licensed RO).

Complementary to hunter_io.py:
  - hunter_io: given (domain, first, last) → verified email for that named person.
    Best when SFC's RO name is already the right contact.
  - apollo_io: given (domain, role_titles) → list of people matching that role.
    Best for finding the CFO / Head of Valuation / Head of Investment Ops at a
    firm — the person who actually signs off on third-party fair-value
    engagements, who is usually NOT the licensed RO on the SFC register.

IMPORTANT — plan tier:
  Apollo's true FREE tier (60 credits/mo) is UI-only; the People-Search API
  endpoint requires the Sales Basic plan ($49/mo) or higher. This module is
  built ready for the paid tier. On the free tier, set APOLLO_API_KEY in .env
  and the module will probe /auth/health; if the API isn't enabled it returns
  status='not_configured' so the caller can fall through to LinkedIn manual.

Quota policy mirrors hunter_io: probe /usage, refuse to call below QUOTA_FLOOR,
fail-closed on probe failure, cache results with TTL.

Endpoints touched:
  - POST /v1/mixed_people/search    (people by org domain + title filter)
  - GET  /v1/auth/health             (key health check; pseudo-quota probe)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # repo root
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.environ.get("APOLLO_API_KEY")
BASE_URL = "https://api.apollo.io"
CACHE_PATH = PROJECT_ROOT / "data" / "apollo_io_cache.json"
LOCK_PATH = PROJECT_ROOT / "data" / ".apollo_io_cache.lock"
TIMEOUT = 20
QUOTA_FLOOR = 5         # reserve last few credits for ad-hoc runs
MISS_TTL_DAYS = 30      # re-query a 'no match' result after this

# Default title set for fair-value decision-makers at HK asset managers.
# Override per-call via find_people(..., titles=[...]).
DEFAULT_DECISION_MAKER_TITLES = [
    "Chief Financial Officer", "CFO",
    "Head of Valuation",
    "Head of Investment Operations", "Head of Operations",
    "Chief Operating Officer", "COO",
    "Finance Director", "Controller",
    "Portfolio Manager",
]

# Typed status enum
STATUS_OK = "ok"
STATUS_NO_MATCH = "no_match"
STATUS_QUOTA_EXHAUSTED = "quota_exhausted"
STATUS_QUOTA_UNKNOWN = "quota_unknown"     # probe failed — fail closed
STATUS_RATE_LIMITED = "rate_limited"       # 429
STATUS_ERROR = "error"
STATUS_NOT_CONFIGURED = "not_configured"   # no key, or API not enabled on plan

_thread_lock = threading.Lock()


# ---------------- cross-process file lock ----------------

class _FileLock:
    """Same shape as hunter_io._FileLock — msvcrt on Win, fcntl on POSIX."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
                        break
                    except OSError:
                        continue
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        return self

    def __exit__(self, *_):
        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                try:
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass


# ---------------- cache I/O ----------------

def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache_merged(updates: dict) -> None:
    """Same merge-on-write pattern as hunter_io: re-read disk, layer updates
    on top, write back. Prevents concurrent processes clobbering each other."""
    with _FileLock(LOCK_PATH):
        cur = _load_cache()
        cur.update(updates)
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            with open(tmp, "rb+") as f:
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            pass
        tmp.replace(CACHE_PATH)


def _cache_key(domain: str, titles: list[str]) -> str:
    norm_titles = "|".join(sorted(t.lower().strip() for t in (titles or [])))
    return f"{(domain or '').lower().strip()}|{norm_titles}"


def _expired(rec: dict) -> bool:
    """Misses TTL out; hits are permanent."""
    if (rec.get("status") or "") != STATUS_NO_MATCH:
        return False
    fetched = rec.get("fetched_at_utc") or ""
    if not fetched:
        return True
    try:
        when = dt.datetime.fromisoformat(fetched.rstrip("Z"))
        age = dt.datetime.now(dt.UTC).replace(tzinfo=None) - when.replace(tzinfo=None)
        return age.days >= MISS_TTL_DAYS
    except Exception:
        return True


# ---------------- quota probe ----------------

def health_check() -> tuple[bool, str]:
    """Return (ok, reason). Free-tier keys often fail here because the API
    isn't enabled on the plan — that's STATUS_NOT_CONFIGURED, not an error."""
    if not API_KEY:
        return False, "no API key"
    try:
        r = requests.get(
            f"{BASE_URL}/v1/auth/health",
            headers={"Cache-Control": "no-cache", "X-Api-Key": API_KEY},
            timeout=TIMEOUT,
        )
    except Exception as e:
        return False, f"network: {e}"
    if r.status_code == 401:
        return False, "401 unauthorized — likely free-plan key without API access"
    if r.status_code == 403:
        return False, "403 forbidden — plan doesn't include API"
    if not r.ok:
        return False, f"HTTP {r.status_code}"
    try:
        body = r.json() or {}
    except Exception:
        return False, "non-JSON response"
    return bool(body.get("is_logged_in", body.get("status") == "ok")), "ok"


def remaining_quota() -> int:
    """Apollo doesn't expose a single 'credits left' endpoint as cleanly as
    Hunter does. We use /v1/usage_stats (paid only) when available; on failure
    return -1 (treated as 'unknown — fail closed' by callers)."""
    if not API_KEY:
        return -1
    try:
        r = requests.get(
            f"{BASE_URL}/v1/usage_stats/api_usage_stats",
            headers={"Cache-Control": "no-cache", "X-Api-Key": API_KEY},
            timeout=TIMEOUT,
        )
        if not r.ok:
            return -1
        data = r.json() or {}
        # Free + paid both expose `requests_left` on the People Search endpoint.
        return int(data.get("people_search_requests_left", -1))
    except Exception:
        return -1


# ---------------- public API ----------------

def find_people(domain: str, titles: list[str] | None = None,
                per_page: int = 5) -> dict | None:
    """Search Apollo for people at `domain` matching any of `titles`.

    Returns:
      {
        "status": "ok" | "no_match" | "quota_exhausted" | "quota_unknown"
                  | "rate_limited" | "error" | "not_configured",
        "people": [
            {
              "name": "Jane Smith",
              "title": "Chief Financial Officer",
              "email": "jane.smith@firm.com" | None,  # masked on free tier
              "email_status": "verified" | "guessed" | "unavailable",
              "linkedin_url": "https://...",
              "city": "Hong Kong",
            },
            ...
        ],
        "fetched_at_utc": "...",
        "quota_remaining_when_fetched": int,
      }
    """
    if not (domain and (titles or DEFAULT_DECISION_MAKER_TITLES)):
        return None
    titles = list(titles) if titles else list(DEFAULT_DECISION_MAKER_TITLES)
    if not API_KEY:
        return _record(STATUS_NOT_CONFIGURED, [], qr=-1)

    key = _cache_key(domain, titles)
    with _thread_lock:
        cache = _load_cache()
        if key in cache and not _expired(cache[key]):
            return cache[key]

    qr = remaining_quota()
    if qr == -1:
        # Probe failed — could be free-tier-no-API, could be transient. Fail closed.
        rec = _record(STATUS_QUOTA_UNKNOWN, [], qr=qr)
        return rec
    if qr < QUOTA_FLOOR:
        return _record(STATUS_QUOTA_EXHAUSTED, [], qr=qr)

    body = {
        "person_titles": titles,
        "q_organization_domains_list": [domain],
        "per_page": per_page,
    }
    try:
        r = requests.post(
            f"{BASE_URL}/v1/mixed_people/search",
            headers={
                "Cache-Control": "no-cache",
                "Content-Type": "application/json",
                "X-Api-Key": API_KEY,
            },
            json=body,
            timeout=TIMEOUT,
        )
    except Exception as e:
        return _record(STATUS_ERROR, [], qr=qr, err=str(e))

    if r.status_code == 402:
        return _record(STATUS_QUOTA_EXHAUSTED, [], qr=qr)
    if r.status_code == 429:
        return _record(STATUS_RATE_LIMITED, [], qr=qr)
    if r.status_code in (401, 403):
        return _record(STATUS_NOT_CONFIGURED, [], qr=qr,
                       err=f"HTTP {r.status_code} — plan likely doesn't include API access")
    if not r.ok:
        return _record(STATUS_ERROR, [], qr=qr, err=f"HTTP {r.status_code}")

    try:
        data = r.json() or {}
    except Exception as e:
        return _record(STATUS_ERROR, [], qr=qr, err=f"json: {e}")

    people = []
    for p in (data.get("people") or [])[:per_page]:
        people.append({
            "name": p.get("name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "title": p.get("title") or "",
            "email": p.get("email"),
            "email_status": p.get("email_status") or "unavailable",
            "linkedin_url": p.get("linkedin_url") or "",
            "city": p.get("city") or "",
            "country": p.get("country") or "",
        })

    rec = _record(
        STATUS_OK if people else STATUS_NO_MATCH,
        people, qr=qr,
    )
    _save_cache_merged({key: rec})
    return rec


def _record(status: str, people: list[dict], *, qr: int = -1,
            err: str | None = None) -> dict:
    rec = {
        "status": status,
        "people": people,
        "source": "apollo.io",
        "fetched_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "quota_remaining_when_fetched": qr,
    }
    if err:
        rec["error"] = err
    return rec


def main():
    """CLI: python apollo_io.py <domain> [title1] [title2]..."""
    import sys
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} <domain> [title1] [title2]...")
        ok, reason = health_check()
        print(f"key configured: {bool(API_KEY)}  health: {ok} ({reason})")
        print(f"quota remaining (rough): {remaining_quota()}")
        sys.exit(1)
    domain = sys.argv[1]
    titles = sys.argv[2:] or None
    rec = find_people(domain, titles)
    print(json.dumps(rec, indent=2))


if __name__ == "__main__":
    main()
