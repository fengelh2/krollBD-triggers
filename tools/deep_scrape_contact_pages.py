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
  - Stamps deep_scrape_attempted_utc (idempotent — re-runs skip done firms)

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
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from classify_strategy import (  # noqa: E402
    OUT_PATH,
    PAGES_DIR,
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

NEW_COL = "deep_scrape_attempted_utc"


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
    already_done = bool((row.get(NEW_COL) or "").strip())
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
    cur = {e.strip().lower() for e in existing_csv.split(",") if e.strip()}
    for e in new_list:
        cur.add(e.lower())
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

    rows = list(csv.DictReader(open(OUT_PATH, encoding="utf-8-sig")))
    if not rows:
        print("no rows in classification CSV", file=sys.stderr)
        sys.exit(1)

    fieldnames = list(rows[0].keys())
    if NEW_COL not in fieldnames:
        fieldnames.append(NEW_COL)
        for r in rows:
            r[NEW_COL] = ""

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
    now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")

    for i, r in enumerate(candidates, 1):
        ceref = r["ceref"]
        url = r["website_url"].strip()
        fd = domain_of(url)
        print(f"[{i}/{len(candidates)}] {ceref}  {url}")
        try:
            md, paths = fetch_contact_pages(url)
        except Exception as e:
            print(f"  ERROR: {e}")
            r[NEW_COL] = now + " (error)"
            continue

        fc_calls_est += len(CONTACT_PATHS)
        if md:
            pages_fetched_total += len(paths)
            named, generic = extract_emails_from_md(md, fd)
            if named or generic:
                old_named = r.get("emails_on_site", "")
                old_generic = r.get("generic_emails_on_site", "")
                r["emails_on_site"] = merge_emails(old_named, named)
                r["generic_emails_on_site"] = merge_emails(old_generic, generic)
                found_emails += 1
                found_named += len(named)
                found_generic += len(generic)
                print(f"  + emails: named={named} generic={generic} (paths={paths})")

                cache_path = PAGES_DIR / f"{ceref}.md"
                with open(cache_path, "a", encoding="utf-8") as f:
                    f.write(f"\n\n---\n# deep_scrape {now}\n")
                    f.write(md)
            else:
                print(f"  - no emails (paths fetched: {paths})")
        else:
            print("  - no contact pages reachable")

        r[NEW_COL] = now

        if i % 10 == 0:
            write_csv(rows, fieldnames)

    write_csv(rows, fieldnames)

    print()
    print(f"firms processed:     {len(candidates)}")
    print(f"firms with new email:{found_emails}")
    print(f"  named added:       {found_named}")
    print(f"  generic added:     {found_generic}")
    print(f"contact pages fetched: {pages_fetched_total}")
    print(f"upper-bound fetch calls: {fc_calls_est}  (plain-HTTP first, Firecrawl only on thin)")


def write_csv(rows, fieldnames):
    tmp = OUT_PATH.with_suffix(".csv.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    tmp.replace(OUT_PATH)


if __name__ == "__main__":
    main()
