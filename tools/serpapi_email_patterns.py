"""Bulk SerpAPI aggregator-pattern discovery.

For each BD-relevant firm with a verified/probable site but no email captured,
query SerpAPI for `"<domain>" email format` (aggregator-biased) and extract the
firm's email pattern from any RocketReach / Hunter / SignalHire / Prospeo /
ContactOut snippet that surfaces.

Once you have the pattern (e.g., "first.last", "flast", "firstlast") you can
apply it to every known RO name from the SFC register without burning Hunter
credits — that's the big leverage move.

This tool only DISCOVERS the pattern. To apply it to ROs, run a separate
`apply_pattern_to_ros.py` (not built yet) or do it inline in the publisher's
email_candidates() path.

Concurrency-safe writes:
  - This tool owns ONLY these columns: email_pattern_inferred,
    email_pattern_source, email_pattern_attempted_utc.
  - Re-reads CSV from disk on every checkpoint, merges its owned columns,
    writes back atomically. Safe to run alongside deep_scrape_contact_pages.py.

Usage:
  python tools/serpapi_email_patterns.py --scope verified-bd --dry-run
  python tools/serpapi_email_patterns.py --scope verified-bd
  python tools/serpapi_email_patterns.py --scope probable-bd --limit 50
  python tools/serpapi_email_patterns.py --cerefs ABG072,AUN967 --force
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from find_email_via_search import query_firm_pattern  # noqa: E402
from classify_strategy import canonical_fieldnames  # noqa: E402

CSV_PATH = PROJECT_ROOT / "data" / "strategy_classification.csv"
OVERRIDES_PATH = PROJECT_ROOT / "data" / "website_overrides.csv"
WORKLIST_PATH = PROJECT_ROOT / "data" / "email_patterns_discovered.csv"

PATTERN_COL = "email_pattern_inferred"
SOURCE_COL = "email_pattern_source"
STAMP_COL = "email_pattern_attempted_utc"
OWNED_COLS = {PATTERN_COL, SOURCE_COL, STAMP_COL}


def domain_of(url: str) -> str:
    if not url:
        return ""
    host = urlparse(url if "://" in url else "https://" + url).netloc
    return (host or "").lower().lstrip("www.").split("/", 1)[0]


def is_bd(r: dict) -> bool:
    return (r.get("illiquid_book_likelihood") or "").lower() in ("high", "medium")


def wa(r: dict) -> str:
    return (r.get("website_accuracy") or "").strip().lower()


def has_email(r: dict) -> bool:
    return bool((r.get("emails_on_site") or "").strip()
                or (r.get("generic_emails_on_site") or "").strip())


def in_scope(r: dict, scope: str) -> bool:
    if not is_bd(r) or not (r.get("website_url") or "").strip():
        return False
    if scope == "verified-bd":
        return wa(r) == "verified"
    if scope == "probable-bd":
        return wa(r) in ("verified", "probable")
    if scope == "hunter-zero":
        return wa(r) in ("verified", "probable") and not has_email(r)
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
    """Re-read from disk, apply only OWNED_COLS deltas, write atomically.
    Prevents clobbering concurrent deep_scrape / classify writes."""
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
    ap.add_argument("--scope", choices=["verified-bd", "probable-bd", "hunter-zero"],
                    default="hunter-zero")
    ap.add_argument("--cerefs", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="re-query even if email_pattern_attempted_utc is set")
    args = ap.parse_args()

    rows = _read_csv()
    if not rows:
        print("empty CSV", file=sys.stderr); sys.exit(1)
    fieldnames = list(rows[0].keys())
    for col in OWNED_COLS:
        if col not in fieldnames:
            fieldnames.append(col)
            for r in rows:
                r[col] = ""

    skip_set = _load_skip_set()
    target_cerefs = {c.strip() for c in args.cerefs.split(",") if c.strip()}

    if target_cerefs:
        candidates = [r for r in rows if r["ceref"] in target_cerefs]
    else:
        candidates = [r for r in rows if in_scope(r, args.scope)
                      and r["ceref"] not in skip_set]
    if not args.force:
        candidates = [r for r in candidates if not (r.get(STAMP_COL) or "").strip()]

    print(f"scope={args.scope} candidates={len(candidates)}", file=sys.stderr)
    if args.limit:
        candidates = candidates[:args.limit]
        print(f"  limited to {len(candidates)}", file=sys.stderr)

    if args.dry_run:
        for r in candidates[:30]:
            print(f"  {r['ceref']:8s} {r['name_en'][:42]:44s} {r['website_url'][:55]}")
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
        d = domain_of(r["website_url"])
        if not d:
            now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
            deltas[ceref] = {STAMP_COL: now, PATTERN_COL: "", SOURCE_COL: "no_domain"}
            continue
        print(f"[{i}/{len(candidates)}] {ceref}  {d}", file=sys.stderr)
        try:
            pattern, evidence, _all_snippets = query_firm_pattern(d)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            errors += 1
            now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
            deltas[ceref] = {STAMP_COL: now, PATTERN_COL: "", SOURCE_COL: f"error:{type(e).__name__}"}
            continue

        now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        if pattern:
            hits += 1
            ev = (evidence or "")[:200]
            deltas[ceref] = {STAMP_COL: now, PATTERN_COL: pattern, SOURCE_COL: ev}
            r[PATTERN_COL] = pattern
            r[SOURCE_COL] = ev
            print(f"  + pattern: {pattern}", file=sys.stderr)
            worklist.append({
                "ceref": ceref, "firm": r["name_en"], "domain": d,
                "pattern": pattern, "evidence": ev,
            })
        else:
            no_match += 1
            deltas[ceref] = {STAMP_COL: now, PATTERN_COL: "", SOURCE_COL: "no_pattern"}
        # Gentle pacing to avoid SerpAPI 429
        time.sleep(0.2)
        if i % 10 == 0:
            _checkpoint(deltas, fieldnames)

    _checkpoint(deltas, fieldnames)

    # Worklist export for downstream pattern-apply
    if worklist:
        cols = ["ceref", "firm", "domain", "pattern", "evidence"]
        WORKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with WORKLIST_PATH.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for row in worklist:
                w.writerow({k: row.get(k, "") for k in cols})
        print(f"worklist: wrote {len(worklist)} firms → {WORKLIST_PATH}", file=sys.stderr)

    print(f"\nprocessed: {len(candidates)}", file=sys.stderr)
    print(f"  patterns found: {hits} ({100*hits/max(1,len(candidates)):.0f}%)", file=sys.stderr)
    print(f"  no_pattern:     {no_match}", file=sys.stderr)
    print(f"  errors:         {errors}", file=sys.stderr)


if __name__ == "__main__":
    main()
