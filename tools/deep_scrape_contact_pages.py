"""Deeper scrape for firms with verified/probable websites but no emails captured.

The classifier only fetches the homepage (and /about fallback for thin pages).
Many firms keep contact info on /contact, /team, /people, /leadership pages.

This tool:
  - Reads strategy_classification.csv
  - Filters to (website_accuracy in scope) AND no emails AND has website_url
  - For each firm, fetches the candidate contact paths
  - Extracts emails (firm-domain-filtered, same logic as classifier)
  - Appends discovered pages to firm_pages/{ceref}.md cache
  - Merges new emails into emails_on_site / generic_emails_on_site
  - Stamps deep_scrape_attempted_utc + deep_scrape_status (idempotent)

Concurrency-safety:
  - Owns only these columns: emails_on_site (union-only), generic_emails_on_site
    (union-only), deep_scrape_attempted_utc, deep_scrape_status.
  - Before each checkpoint, re-reads the on-disk CSV and merges only owned columns
    back into the fresh rows. This prevents clobbering concurrent classify_strategy
    appends or manual Excel edits to non-owned columns.
  - Writes are atomic via tmp + os.replace, with fsync before replace.

Usage:
  python deep_scrape_contact_pages.py --scope verified-bd --dry-run
  python deep_scrape_contact_pages.py --scope verified-bd
  python deep_scrape_contact_pages.py --scope verified-bd --limit 10
  python deep_scrape_contact_pages.py --force --cerefs AAA137,AAA529
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

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))

from classify_strategy import (  # noqa: E402
    OUT_PATH,
    PAGES_DIR,
    canonical_fieldnames,
    extract_emails_from_md,
    robots_allowed,
    scrape_firecrawl,
)

CONTACT_PATHS = [
    "/contact",
    "/contact-us",
    "/contactus",
    "/team",
    "/our-team",
    "/people",
    "/leadership",
]

STAMP_COL = "deep_scrape_attempted_utc"
STATUS_COL = "deep_scrape_status"
# Columns this tool is allowed to update; everything else is read-only.
OWNED_COLS = {"emails_on_site", "generic_emails_on_site", STAMP_COL, STATUS_COL}

# deep_scrape_status enum:
#   ok             - emails found
#   no_emails      - HTML reached but no @ matches
#   no_pages       - every candidate path was unreachable / robots-disallowed
#   error          - exception during fetch / parsing
#   skipped_no_url - row had no website_url
STATUS_OK = "ok"
STATUS_NO_EMAILS = "no_emails"
STATUS_NO_PAGES = "no_pages"
STATUS_ERROR = "error"


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return host.lower()
    except Exception:
        return ""


def in_scope(row: dict, scope: str) -> bool:
    acc = (row.get("website_accuracy") or "").strip()
    illiq = (row.get("illiquid_book_likelihood") or "").strip()
    bd = illiq in ("high", "medium")
    if scope == "verified":
        return acc == "verified"
    if scope == "verified-high":
        return acc == "verified" and illiq == "high"
    if scope == "verified-bd":
        return acc == "verified" and bd
    if scope == "probable":
        return acc in ("verified", "probable")
    if scope == "probable-high":
        return acc in ("verified", "probable") and illiq == "high"
    if scope == "probable-bd":
        return acc in ("verified", "probable") and bd
    if scope == "unverified-bd":
        return acc in ("suspect", "unverified") and bd
    raise SystemExit(f"unknown --scope {scope!r}")


def needs_scrape(row: dict) -> bool:
    has_site = bool((row.get("website_url") or "").strip())
    has_email = bool(
        (row.get("emails_on_site") or "").strip()
        or (row.get("generic_emails_on_site") or "").strip()
    )
    already_done = bool((row.get(STAMP_COL) or "").strip())
    return has_site and not has_email and not already_done


def fetch_contact_pages(website_url: str) -> tuple[str, list[str]]:
    """Return (combined_markdown, paths_fetched). Stops once we have enough content."""
    base = website_url.rstrip("/")
    parts = []
    fetched = []
    for path in CONTACT_PATHS:
        candidate = base + path
        if not robots_allowed(candidate):
            continue
        md = scrape_firecrawl(candidate)
        if md and len(md) >= 200:
            parts.append(f"\n\n---\n# {path}\n{md}")
            fetched.append(path)
        time.sleep(0.3)
    return "".join(parts), fetched


def merge_emails(existing_csv: str, new_list: list[str]) -> str:
    """Union the two sources. Guards against malformed tokens (must contain '@')."""
    cur = {e.strip().lower() for e in (existing_csv or "").split(",")
           if e.strip() and "@" in e}
    for e in new_list:
        e = (e or "").strip().lower()
        if e and "@" in e:
            cur.add(e)
    return ",".join(sorted(cur))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--scope",
        choices=["verified", "verified-high", "verified-bd", "probable", "probable-high", "probable-bd", "unverified-bd"],
        default="verified-high",
    )
    ap.add_argument("--limit", type=int, default=0, help="cap on firms processed (0=all)")
    ap.add_argument("--dry-run", action="store_true", help="print candidates, don't fetch")
    ap.add_argument("--force", action="store_true", help="ignore deep_scrape_attempted_utc")
    ap.add_argument("--cerefs", default="", help="comma-separated cerefs to target")
    args = ap.parse_args()

    rows = _read_csv()
    if not rows:
        print("no rows in classification CSV", file=sys.stderr)
        sys.exit(1)

    fieldnames = list(rows[0].keys())
    for col in (STAMP_COL, STATUS_COL):
        if col not in fieldnames:
            fieldnames.append(col)
            for r in rows:
                r[col] = ""

    target_cerefs = {c.strip() for c in args.cerefs.split(",") if c.strip()}

    candidates = []
    for r in rows:
        if target_cerefs:
            if r["ceref"] in target_cerefs:
                candidates.append(r)
            continue
        if not in_scope(r, args.scope):
            continue
        if not args.force and not needs_scrape(r):
            continue
        if args.force and not (r.get("website_url") or "").strip():
            continue
        candidates.append(r)

    print(f"scope={args.scope} candidates={len(candidates)}")
    if args.limit:
        candidates = candidates[: args.limit]
        print(f"limited to {len(candidates)}")

    if args.dry_run:
        for r in candidates[:30]:
            print(f"  {r['ceref']:8s} {r['website_accuracy']:9s} {r['illiquid_book_likelihood']:6s} {r['website_url']}")
        if len(candidates) > 30:
            print(f"  ... +{len(candidates)-30} more")
        return

    found_emails = 0
    found_named = 0
    found_generic = 0
    pages_fetched_total = 0
    fc_calls_est = 0

    # Track per-firm deltas so we only write the columns we own.
    # Key: ceref → dict of owned-column updates.
    deltas: dict[str, dict] = {}

    for i, r in enumerate(candidates, 1):
        ceref = r["ceref"]
        url = (r.get("website_url") or "").strip()
        fd = domain_of(url)
        now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        print(f"[{i}/{len(candidates)}] {ceref}  {url}")

        delta: dict = {STAMP_COL: now}
        try:
            md, paths = fetch_contact_pages(url)
        except Exception as e:
            print(f"  ERROR: {e}")
            delta[STATUS_COL] = STATUS_ERROR
            deltas[ceref] = delta
            continue

        fc_calls_est += len(CONTACT_PATHS)
        if md:
            pages_fetched_total += len(paths)
            named, generic = extract_emails_from_md(md, fd)
            if named or generic:
                # Merge into the in-memory row; will be reconciled with disk at write time.
                delta["emails_on_site"] = merge_emails(r.get("emails_on_site", ""), named)
                delta["generic_emails_on_site"] = merge_emails(r.get("generic_emails_on_site", ""), generic)
                delta[STATUS_COL] = STATUS_OK
                found_emails += 1
                found_named += len(named)
                found_generic += len(generic)
                print(f"  + emails: named={named} generic={generic} (paths={paths})")

                cache_path = PAGES_DIR / f"{ceref}.md"
                with open(cache_path, "a", encoding="utf-8") as f:
                    f.write(f"\n\n---\n# deep_scrape {now}\n")
                    f.write(md)
            else:
                delta[STATUS_COL] = STATUS_NO_EMAILS
                print(f"  - no emails (paths fetched: {paths})")
        else:
            delta[STATUS_COL] = STATUS_NO_PAGES
            print("  - no contact pages reachable")

        # Reflect on in-memory row so subsequent reads (and final write) see it.
        for k, v in delta.items():
            r[k] = v
        deltas[ceref] = delta

        if i % 10 == 0:
            _checkpoint(deltas, fieldnames)

    _checkpoint(deltas, fieldnames)

    print()
    print(f"firms processed:     {len(candidates)}")
    print(f"firms with new email:{found_emails}")
    print(f"  named added:       {found_named}")
    print(f"  generic added:     {found_generic}")
    print(f"contact pages fetched: {pages_fetched_total}")
    print(f"upper-bound fetch calls: {fc_calls_est}  (plain-HTTP first, Firecrawl only on thin)")


def _read_csv() -> list[dict]:
    with open(OUT_PATH, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _checkpoint(deltas: dict[str, dict], fieldnames: list[str]) -> None:
    """Re-read CSV from disk, apply our owned-column deltas only, atomic-replace.

    This is the merge step that prevents clobbering concurrent writes from
    classify_strategy or manual edits to non-owned columns.
    """
    if not deltas:
        return
    fresh_rows = _read_csv()
    fresh_field = list(fresh_rows[0].keys()) if fresh_rows else fieldnames
    # Ensure our owned columns exist in the fieldset we're about to write.
    for col in (STAMP_COL, STATUS_COL):
        if col not in fresh_field:
            fresh_field.append(col)
            for r in fresh_rows:
                r[col] = ""
    # Apply deltas: only the owned columns flow through.
    by_ceref = {r["ceref"]: r for r in fresh_rows}
    for ceref, delta in deltas.items():
        target = by_ceref.get(ceref)
        if target is None:
            continue
        for col in OWNED_COLS:
            if col in delta:
                if col in ("emails_on_site", "generic_emails_on_site"):
                    # Union-only: never downgrade. Merge delta with current disk value.
                    target[col] = merge_emails(target.get(col, ""), [e for e in (delta[col] or "").split(",") if e])
                else:
                    target[col] = delta[col]
    _atomic_write(fresh_rows, fresh_field)


def _atomic_write(rows: list[dict], fieldnames: list[str]) -> None:
    """Write to .tmp, fsync, atomic replace. utf-8 (no BOM) standard.
    Column order is canonicalized so layout stays stable regardless of which
    tool ran last."""
    ordered = canonical_fieldnames(fieldnames)
    tmp = OUT_PATH.with_suffix(".csv.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ordered)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in ordered})
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(OUT_PATH)


# Kept for backwards-compat with any external caller; routes to the new atomic writer.
def write_csv(rows, fieldnames):
    _atomic_write(rows, fieldnames)


if __name__ == "__main__":
    main()
