"""Discover LinkedIn profile URLs for primary ROs via SerpAPI.

For each BD-relevant firm without a primary-RO LinkedIn URL captured, query
SerpAPI for `"<firm name>" "<primary RO name>" linkedin.com/in`. Returns the
top-result LinkedIn profile URL. LinkedIn is heavily indexed → ~60-70% hit
rate expected.

Output: per-firm linkedin_url_primary_ro column + a worklist CSV with all
discovered URLs so Felix can manually shortlist for InMail / connection req
outreach.

Concurrency-safe writes — owns only its columns.

Usage:
  python tools/serpapi_linkedin_discovery.py --scope verified-bd --dry-run
  python tools/serpapi_linkedin_discovery.py --scope verified-bd
  python tools/serpapi_linkedin_discovery.py --scope probable-bd --limit 50
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from find_email_via_search import _serpapi_get  # noqa: E402
from classify_strategy import canonical_fieldnames  # noqa: E402

CSV_PATH = PROJECT_ROOT / "data" / "strategy_classification.csv"
PAIRS_PATH = PROJECT_ROOT / "data" / "snapshots" / "sfc_t9_corp_ros_latest.csv"
OVERRIDES_PATH = PROJECT_ROOT / "data" / "website_overrides.csv"
WORKLIST_PATH = PROJECT_ROOT / "data" / "linkedin_discovered.csv"

LINKEDIN_COL = "linkedin_url_primary_ro"
STAMP_COL = "linkedin_lookup_attempted_utc"
OWNED_COLS = {LINKEDIN_COL, STAMP_COL}


def is_bd(r: dict) -> bool:
    return (r.get("illiquid_book_likelihood") or "").lower() in ("high", "medium")


def wa(r: dict) -> str:
    return (r.get("website_accuracy") or "").strip().lower()


def in_scope(r: dict, scope: str) -> bool:
    if not is_bd(r):
        return False
    if scope == "verified-bd":
        return wa(r) == "verified"
    if scope == "probable-bd":
        return wa(r) in ("verified", "probable")
    raise SystemExit(f"unknown --scope {scope!r}")


def _load_skip_set() -> set[str]:
    if not OVERRIDES_PATH.exists():
        return set()
    out: set[str] = set()
    with OVERRIDES_PATH.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            ce = (r.get("ceref") or "").strip()
            if ce and str(r.get("skip_enrichment", "")).strip().lower() in ("1", "true", "yes", "y"):
                out.add(ce)
    return out


def _read_csv() -> list[dict]:
    with CSV_PATH.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _atomic_write(rows: list[dict], fieldnames: list[str]) -> None:
    ordered = canonical_fieldnames(fieldnames)
    tmp = CSV_PATH.with_suffix(".csv.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ordered)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in ordered})
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(CSV_PATH)


def _checkpoint(deltas: dict[str, dict], fieldnames: list[str]) -> None:
    if not deltas:
        return
    fresh_rows = _read_csv()
    fresh_field = list(fresh_rows[0].keys()) if fresh_rows else fieldnames
    for col in OWNED_COLS:
        if col not in fresh_field:
            fresh_field.append(col)
            for r in fresh_rows:
                r[col] = ""
    by_ceref = {r["ceref"]: r for r in fresh_rows}
    for ceref, delta in deltas.items():
        target = by_ceref.get(ceref)
        if target is None:
            continue
        for col in OWNED_COLS:
            if col in delta:
                target[col] = delta[col]
    _atomic_write(fresh_rows, fresh_field)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["verified-bd", "probable-bd"],
                    default="verified-bd")
    ap.add_argument("--cerefs", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    rows = _read_csv()
    fieldnames = list(rows[0].keys())
    for col in OWNED_COLS:
        if col not in fieldnames:
            fieldnames.append(col)
            for r in rows:
                r[col] = ""

    pairs = list(csv.DictReader(PAIRS_PATH.open(encoding="utf-8-sig")))
    primary_ro_by_corp: dict[str, dict] = {}
    for p in pairs:
        primary_ro_by_corp.setdefault(p["corp_ceref"], p)

    skip_set = _load_skip_set()
    target_cerefs = {c.strip() for c in args.cerefs.split(",") if c.strip()}

    if target_cerefs:
        candidates = [r for r in rows if r["ceref"] in target_cerefs]
    else:
        candidates = [r for r in rows if in_scope(r, args.scope)
                      and r["ceref"] not in skip_set
                      and r["ceref"] in primary_ro_by_corp]
    if not args.force:
        candidates = [r for r in candidates if not (r.get(STAMP_COL) or "").strip()]

    print(f"scope={args.scope} candidates={len(candidates)}", file=sys.stderr)
    if args.limit:
        candidates = candidates[:args.limit]
        print(f"  limited to {len(candidates)}", file=sys.stderr)

    if args.dry_run:
        for r in candidates[:30]:
            ro = primary_ro_by_corp.get(r["ceref"], {})
            print(f"  {r['ceref']:8s} {r['name_en'][:36]:38s} ro={ro.get('ro_full_name','')}")
        if len(candidates) > 30:
            print(f"  ... +{len(candidates) - 30} more")
        return

    deltas: dict[str, dict] = {}
    worklist: list[dict] = []
    hits = 0
    no_match = 0
    errors = 0
    for i, r in enumerate(candidates, 1):
        ceref = r["ceref"]
        ro = primary_ro_by_corp.get(ceref, {})
        ro_name = (ro.get("ro_full_name") or "").strip()
        if not ro_name:
            continue
        print(f"[{i}/{len(candidates)}] {ceref}  {ro_name}", file=sys.stderr)
        query = f'"{r["name_en"]}" "{ro_name}" linkedin.com/in'
        data, status = _serpapi_get({"q": query, "num": 5, "hl": "en"})
        now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        if status != "ok":
            errors += 1
            deltas[ceref] = {STAMP_COL: now, LINKEDIN_COL: ""}
            continue
        # Pull first organic result that's a LinkedIn /in/ profile
        url = ""
        for item in data.get("organic_results", []):
            link = (item.get("link") or "")
            if "linkedin.com/in/" in link:
                url = link
                break
        if url:
            hits += 1
            deltas[ceref] = {STAMP_COL: now, LINKEDIN_COL: url}
            r[LINKEDIN_COL] = url
            worklist.append({
                "ceref": ceref, "firm": r["name_en"],
                "ro_name": ro_name, "ro_ceref": ro.get("ro_ceref", ""),
                "linkedin_url": url,
            })
            print(f"  + {url}", file=sys.stderr)
        else:
            no_match += 1
            deltas[ceref] = {STAMP_COL: now, LINKEDIN_COL: ""}
        time.sleep(0.2)
        if i % 10 == 0:
            _checkpoint(deltas, fieldnames)

    _checkpoint(deltas, fieldnames)

    if worklist:
        cols = ["ceref", "firm", "ro_name", "ro_ceref", "linkedin_url"]
        WORKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with WORKLIST_PATH.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for row in worklist:
                w.writerow({k: row.get(k, "") for k in cols})
        print(f"worklist: {len(worklist)} rows → {WORKLIST_PATH}", file=sys.stderr)

    print(f"\nprocessed: {len(candidates)}", file=sys.stderr)
    print(f"  hits:      {hits} ({100*hits/max(1,len(candidates)):.0f}%)", file=sys.stderr)
    print(f"  no_match:  {no_match}", file=sys.stderr)
    print(f"  errors:    {errors}", file=sys.stderr)


if __name__ == "__main__":
    main()
