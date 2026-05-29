"""Snapshot the SFC public register for Type 9 (Asset Management).

Writes three CSVs per run under data/snapshots/ (relative to repo root):
  sfc_t9_corps_<DATE>.csv         — one row per active Type 9 corporation
  sfc_t9_corp_ros_<DATE>.csv      — one row per (corp, RO) pair
  sfc_t9_individuals_<DATE>.csv   — one row per active Type 9 licensed individual

Source: https://apps.sfc.hk/publicregWeb/  (POST /searchByRaJson, GET /corp/{ceref}/ro)

Honesty notes (verified 2026-05-28):
  - SFC publishes firm WEBSITES on the /corp/{ceref}/addresses tab (embedded as
    `websiteData = [...]` in the page HTML). ~53% of active Type 9 firms have one.
    This is the authoritative source — use it before any Google search.
  - SFC does NOT expose email / phone publicly. Those fields exist in the JSON
    schema but are null in every sampled record.
  - "Active" means hasActiveLicence=Y under the SFO. AMLO-only "deemed" licences are
    written to a separate column but not filtered out.
  - Detail-page `raDetailData` is the source of truth for effective dates per RA.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

BASE = "https://apps.sfc.hk/publicregWeb"
LIST_URL = f"{BASE}/searchByRaJson"
CORP_DETAIL_URL = f"{BASE}/corp/{{ceref}}/details"
CORP_RO_URL = f"{BASE}/corp/{{ceref}}/ro"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
UA = "Mozilla/5.0 (krollBD SFC snapshot; contact fengelh@gmail.com)"

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # repo root
SNAP_DIR = PROJECT_ROOT / "data" / "snapshots"


def fetch_list(session: requests.Session, ratype: str, role: str, letter: str) -> list[dict]:
    """Pull one (RA-type, role, letter) slice. Uses limit=1000 to fit in one page.

    Retries up to 4 times on connection/read errors with exponential backoff —
    apps.sfc.hk closes idle connections under load.
    """
    last_err = None
    for attempt in range(4):
        try:
            r = session.post(
                LIST_URL,
                data={
                    "ratype": ratype, "licstatus": "all", "roleType": role,
                    "nameStartLetter": letter, "locale": "en",
                    "page": "1", "start": "0", "limit": "1000",
                },
                headers={"User-Agent": UA}, timeout=60,
            )
            r.raise_for_status()
            break
        except (requests.ConnectionError, requests.ReadTimeout) as e:
            last_err = e
            time.sleep(2 ** attempt)
            continue
    else:
        raise last_err
    payload = r.json()
    items = payload.get("items", [])
    total = payload.get("totalCount", 0)
    if len(items) < total:
        # Safety net: if a single letter ever exceeds 1000, page through.
        for start in range(1000, total, 1000):
            r2 = session.post(
                LIST_URL,
                data={
                    "ratype": ratype, "licstatus": "all", "roleType": role,
                    "nameStartLetter": letter, "locale": "en",
                    "page": str(start // 1000 + 1), "start": str(start), "limit": "1000",
                },
                headers={"User-Agent": UA}, timeout=60,
            )
            r2.raise_for_status()
            items.extend(r2.json().get("items", []))
    return items


def fetch_all_list(ratype: str, role: str) -> list[dict]:
    """Iterate A-Z and combine, deduped on ceref."""
    out: dict[str, dict] = {}
    with requests.Session() as s:
        for letter in LETTERS:
            items = fetch_list(s, ratype, role, letter)
            for it in items:
                out[it["ceref"]] = it
            print(f"  {role:11s} ratype={ratype} letter={letter}: {len(items):4d} items (cum {len(out)})", file=sys.stderr)
            time.sleep(0.15)
    return list(out.values())


RO_RAW_RE = re.compile(r"var\s+rorawData\s*=\s*(\[.*?\]);", re.S)


def fetch_corp_ros(session: requests.Session, ceref: str) -> list[dict]:
    """Return list of RO records for a corp (parsed from the embedded JSON var)."""
    r = session.get(CORP_RO_URL.format(ceref=ceref), headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    m = RO_RAW_RE.search(r.text)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return []


def enrich_ros(corps: list[dict], workers: int = 10) -> list[dict]:
    """Fetch /ro for every corp; return flattened (corp_ceref, ro_*) rows."""
    rows: list[dict] = []
    session = requests.Session()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_corp_ros, session, c["ceref"]): c for c in corps}
        for i, fut in enumerate(as_completed(futs), 1):
            corp = futs[fut]
            try:
                ros = fut.result()
            except Exception as e:
                print(f"  [warn] {corp['ceref']} {corp['name']}: {e}", file=sys.stderr)
                ros = []
            for ro in ros:
                ra_types = sorted({
                    str(d.get("actType"))
                    for d in (ro.get("regulatedActivities") or [])
                    if d.get("actType") is not None
                })
                fn = ro.get("fullName") or ""
                first_short, first_full, last = parse_person_name(fn)
                rows.append({
                    "corp_ceref": corp["ceref"],
                    "corp_name": corp["name"],
                    "ro_ceref": ro.get("ceRef"),
                    "ro_full_name": fn,
                    "ro_first_short": first_short,
                    "ro_first_full": first_full,
                    "ro_last": last,
                    "ro_name_chi": ro.get("entityNameChi"),
                    "ro_ra_types": ",".join(ra_types),
                })
            if i % 100 == 0:
                print(f"  RO enrichment: {i}/{len(corps)}", file=sys.stderr)
    return rows


def flatten_addr(it: dict) -> str:
    a = it.get("address") or {}
    return (a.get("fullAddress") or "").strip()


# ---------------- SFC-registered website (per-corp /addresses fetch) ----------------
# The SFC public register stores firm-submitted websites on the /addresses tab,
# embedded as `websiteData = [{"website":"..."}]`. ~53% of active Type 9 firms
# have one registered; this is the authoritative source and beats any
# Google-search guess. Adds one HTTP per corp to the snapshot.

WEBSITE_RE = re.compile(r'websiteData\s*=\s*(\[[^;]*\])')


def fetch_sfc_website(session: requests.Session, ceref: str) -> str:
    """Return the firm's self-registered website URL from SFC, or '' if none."""
    try:
        r = session.get(
            f"https://apps.sfc.hk/publicregWeb/corp/{ceref}/addresses?locale=en",
            headers={"User-Agent": UA}, timeout=20,
        )
        if r.status_code != 200:
            return ""
        m = WEBSITE_RE.search(r.text)
        if not m:
            return ""
        try:
            data = json.loads(m.group(1))
        except Exception:
            return ""
        for d in data:
            if isinstance(d, dict) and d.get("website"):
                site = d["website"].strip()
                # Normalize: prepend https:// if user submitted bare domain
                if site and not site.startswith(("http://", "https://")):
                    site = "https://" + site.lstrip("/")
                return site
        return ""
    except Exception:
        return ""


# ---------------- natural-name cleaner ----------------
# Strips legal/geographic suffixes that aren't how the firm is referred to in
# practice. Used downstream for SerpAPI queries + email salutations.
# Manual overrides live in data/name_natural_overrides.csv (ceref,name_natural).

import re as _re

_NATURAL_SUFFIX_PATTERNS = [
    # "Limited, The" / "Co., Ltd." style
    r",\s*The$",
    r",?\s*(?:Limited|Ltd\.?|L\.?L\.?C\.?|L\.?P\.?|Inc\.?|Corporation|Corp\.?|Co\.?)$",
    # parenthesised geographic qualifiers
    r"\s*\((?:Hong Kong|HK|H\.K\.|Asia|Asia[ -]Pacific|APAC|Far East|"
        r"International|Global|North Asia|Greater China|China)\)$",
    # trailing standalone geographic words
    r"\s+(?:Hong\s*Kong|HK|Asia|Asia[ -]Pacific|APAC|Far\s*East|"
        r"International|North\s*Asia|Greater\s*China)$",
    # "Asset Management" / "Investment Management" / "Capital Management"
    r"\s+(?:Asset|Investment|Capital|Fund|Securities)\s+Management$",
]


def _strip_iter(s: str) -> str:
    """Apply suffix-stripping patterns repeatedly until stable, then trim
    dangling conjunctions / connectors."""
    prev = None
    while s != prev:
        prev = s
        for pat in _NATURAL_SUFFIX_PATTERNS:
            s = _re.sub(pat, "", s, flags=_re.I).strip()
    # Strip trailing connectors left dangling by suffix removal:
    # "CR Wealth and Asset Management" -> "CR Wealth and" -> "CR Wealth"
    s = _re.sub(r"\s+(and|or|&|of|the)\s*$", "", s, flags=_re.I).strip()
    s = s.rstrip(",.;:-&")
    return s


_OVERRIDES_PATH = PROJECT_ROOT / "data" / "name_natural_overrides.csv"


def _load_overrides() -> dict[str, str]:
    if not _OVERRIDES_PATH.exists():
        return {}
    out = {}
    with _OVERRIDES_PATH.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("ceref") and r.get("name_natural"):
                out[r["ceref"].strip()] = r["name_natural"].strip()
    return out


# ---------------- person-name parser ----------------
# SFC convention: 'LAST First Middle' (e.g. 'NETO Florian Andre Jean')
# Or: 'LAST First, WesternNickname' (e.g. 'AU Chong Kit, Stanley')

def parse_person_name(full: str) -> tuple[str, str, str]:
    """Return (first_short, first_full, last) — title-cased, ready for email use.

    Heuristics:
      - Trailing ', Nickname' is the Western preferred name → use as first_short
      - Leading ALL-CAPS token = surname; rest = given name(s)
      - first_short defaults to first given name if it looks Western (≥4 chars)
      - For short Chinese names (e.g. 'Ka Nam') uses the full given name as short
    """
    if not full:
        return "", "", ""
    s = full.strip()
    nickname = None
    if "," in s:
        head, tail = s.rsplit(",", 1)
        tail = tail.strip()
        if len(tail.split()) == 1 and len(tail) >= 3 and tail.replace("'", "").isalpha():
            nickname = tail
            s = head.strip()
    tokens = [t for t in s.split() if t]
    if not tokens:
        return "", "", ""
    if len(tokens) >= 2 and tokens[0].isupper() and tokens[0].isalpha():
        last = tokens[0].title()
        first_tokens = tokens[1:]
    else:
        last = tokens[-1].title()
        first_tokens = tokens[:-1]
    first_full = " ".join(t.title() for t in first_tokens)
    if nickname:
        first_short = nickname.title()
    elif len(first_tokens) >= 3:
        # 3+ given tokens → Western middle-name pattern, take just the first
        first_short = first_tokens[0].title()
    elif len(first_tokens) == 2:
        # Heuristic for 2-token given names:
        # - Western multi-name like "Stephen Edward", "Christopher John":
        #   both tokens look "long-enough" (each >=5 chars) → use first only
        # - Chinese/Korean dual-syllable like "Ka Nam", "Kwong Yiu", "Tae Won":
        #   at least one short token → both go together as the address form
        t1, t2 = first_tokens[0], first_tokens[1]
        if len(t1) >= 5 and len(t2) >= 5:
            first_short = t1.title()
        else:
            first_short = first_full
    else:
        # 1 token — use as-is
        first_short = first_full
    return first_short, first_full, last


def natural_name(legal: str, ceref: str | None = None,
                 overrides: dict[str, str] | None = None) -> str:
    """Return the firm's common-usage name (e.g. 'BlackRock Asset Management
    North Asia Limited' -> 'BlackRock').

    Safety rails:
    - never shrink below 6 chars (avoids 'JK', 'PA', etc. that are too generic)
    - if cleaning would over-strip, back off to the next-less-stripped result
    - if even that's too short, fall back to a sensibly-stripped legal name
      (only the trailing 'Limited' / 'Ltd.' removed)
    """
    if overrides is None:
        overrides = _load_overrides()
    if ceref and ceref in overrides:
        return overrides[ceref]
    raw = (legal or "").strip()
    if not raw:
        return raw
    cleaned = _strip_iter(raw)
    if len(cleaned) >= 6:
        return cleaned
    # Over-stripped — fall back to just removing trailing legal-form words
    fallback = _re.sub(
        r",?\s*(?:Limited|Ltd\.?|L\.?L\.?C\.?|L\.?P\.?|Inc\.?|Corporation|Corp\.?|Co\.?)$",
        "", raw, flags=_re.I,
    ).strip()
    fallback = _re.sub(r",\s*The$", "", fallback, flags=_re.I).strip()
    return fallback if len(fallback) >= 4 else raw


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    print(f"wrote {path}  ({len(rows)} rows)", file=sys.stderr)


def rotate_to_prev(ratype: str) -> None:
    """Move existing *_latest.csv → *_prev.csv (atomic per-file). Called at the
    top of a new scrape so we always preserve last week for diffing."""
    for suffix in ("corps", "individuals", "corp_ros"):
        latest = SNAP_DIR / f"sfc_t{ratype}_{suffix}_latest.csv"
        prev = SNAP_DIR / f"sfc_t{ratype}_{suffix}_prev.csv"
        if latest.exists():
            if prev.exists():
                prev.unlink()
            latest.rename(prev)
            print(f"  rotated: {suffix}_latest → {suffix}_prev", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description="Snapshot SFC Type 9 register.")
    ap.add_argument("--skip-ros", action="store_true",
                    help="Skip the per-corp RO enrichment (much faster, no people data).")
    ap.add_argument("--workers", type=int, default=10, help="RO-fetch thread pool size.")
    ap.add_argument("--ratype", default="9", help="SFC RA type code (default 9 = Asset Management).")
    ap.add_argument("--no-rotate", action="store_true",
                    help="Do NOT rotate latest→prev (useful for testing without losing the diff baseline).")
    args = ap.parse_args()

    if not args.no_rotate:
        rotate_to_prev(args.ratype)

    print(f"[1/3] Corporations (ratype={args.ratype}, active)...", file=sys.stderr)
    corps_raw = fetch_all_list(args.ratype, "corporation")
    overrides = _load_overrides()

    # Enrich each corp with its SFC-registered website (parallel HTTP fetch).
    print(f"  Fetching SFC-registered websites ({len(corps_raw)} corps, {args.workers} workers)...",
          file=sys.stderr)
    session = requests.Session()
    sites: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_sfc_website, session, c["ceref"]): c["ceref"]
                for c in corps_raw}
        for i, fut in enumerate(as_completed(futs), 1):
            ceref = futs[fut]
            try:
                sites[ceref] = fut.result()
            except Exception:
                sites[ceref] = ""
            if i % 250 == 0:
                print(f"  websites: {i}/{len(corps_raw)}", file=sys.stderr)
    with_site = sum(1 for v in sites.values() if v)
    print(f"  → {with_site}/{len(corps_raw)} have SFC-registered website ({with_site*100//max(1,len(corps_raw))}%)",
          file=sys.stderr)

    corp_rows = [{
        "ceref": c["ceref"],
        "name_en": c["name"],
        "name_natural": natural_name(c["name"], c["ceref"], overrides),
        "name_chi": c.get("nameChi") or "",
        "address": flatten_addr(c),
        "website_url_sfc": sites.get(c["ceref"], ""),
        "has_active_licence": c.get("hasActiveLicence") or "",
        "has_active_licence_amlo": c.get("hasActiveLicenceAmlo") or "",
        "is_deemed_licence": c.get("isDeemedLicence") or "",
    } for c in corps_raw]
    write_csv(
        SNAP_DIR / f"sfc_t{args.ratype}_corps_latest.csv",
        corp_rows,
        ["ceref", "name_en", "name_natural", "name_chi", "address",
         "website_url_sfc",
         "has_active_licence", "has_active_licence_amlo", "is_deemed_licence"],
    )

    print(f"[2/3] Individuals (ratype={args.ratype}, active)...", file=sys.stderr)
    indiv_raw = fetch_all_list(args.ratype, "individual")
    indiv_rows = []
    for i in indiv_raw:
        first_short, first_full, last = parse_person_name(i["name"])
        indiv_rows.append({
            "ceref": i["ceref"],
            "name_en": i["name"],
            "first_short": first_short,
            "first_full": first_full,
            "last": last,
            "name_chi": i.get("nameChi") or "",
            "has_active_licence": i.get("hasActiveLicence") or "",
            "is_active_eo": i.get("isActiveEo") or "",
        })
    write_csv(
        SNAP_DIR / f"sfc_t{args.ratype}_individuals_latest.csv",
        indiv_rows,
        ["ceref", "name_en", "first_short", "first_full", "last", "name_chi",
         "has_active_licence", "is_active_eo"],
    )

    if args.skip_ros:
        print("[3/3] Skipping RO enrichment (--skip-ros).", file=sys.stderr)
        return
    print(f"[3/3] Per-corp RO enrichment ({len(corps_raw)} corps, {args.workers} workers)...", file=sys.stderr)
    ro_rows = enrich_ros(corps_raw, workers=args.workers)
    write_csv(
        SNAP_DIR / f"sfc_t{args.ratype}_corp_ros_latest.csv",
        ro_rows,
        ["corp_ceref", "corp_name", "ro_ceref", "ro_full_name",
         "ro_first_short", "ro_first_full", "ro_last",
         "ro_name_chi", "ro_ra_types"],
    )


if __name__ == "__main__":
    main()
