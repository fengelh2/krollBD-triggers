"""Run Apollo.io people-search on a cohort to surface DECISION-MAKERS.

Complementary to hunter_for_cohort.py: Hunter looks up a NAMED person's email;
Apollo finds the person at a role (CFO / Head of Valuation / etc.) where SFC's
licensed RO is NOT the right person to email (the licensed RO is often a
compliance signatory, not the fair-value buyer).

Reads:
  - data/strategy_classification.csv  (for cohort filtering + firm domain)
  - data/website_overrides.csv        (skip_enrichment respect)

Writes:
  - data/apollo_io_cache.json (via apollo_io.find_people)
  - data/strategy_classification.csv (adds apollo_* columns, atomic)
  - data/linkedin_targets_apollo.csv (LinkedIn URLs + role per firm — for
    manual follow-up when Apollo email is masked / no_match)

Scope options:
  --scope verified-bd    BD-relevant + verified site (tightest, ~hunter-cohort)
  --scope probable-bd    BD-relevant + verified or probable site (broader)
  --scope hunter-zero    BD-relevant + site + no named email at all
  --scope only-generic   BD-relevant + site + only generic email captured

Usage:
  python tools/apollo_for_cohort.py --scope verified-bd --dry-run
  python tools/apollo_for_cohort.py --scope verified-bd --limit 10
  python tools/apollo_for_cohort.py --cerefs ABG072,AUN967 --force

PLAN NOTE: Apollo's true free tier (60 credits/mo) is UI-only. The People-
Search API used here requires Sales Basic ($49/mo) or higher. If your key
isn't enabled, this tool returns status='not_configured' for every firm and
writes nothing — safe to run as a probe.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import apollo_io  # noqa: E402
from classify_strategy import canonical_fieldnames  # noqa: E402

CSV_PATH = PROJECT_ROOT / "data" / "strategy_classification.csv"
PAIRS_PATH = PROJECT_ROOT / "data" / "snapshots" / "sfc_t9_corp_ros_latest.csv"
OVERRIDES_PATH = PROJECT_ROOT / "data" / "website_overrides.csv"
LI_EXPORT_DEFAULT = PROJECT_ROOT / "data" / "linkedin_targets_apollo.csv"

APOLLO_AT_COL = "apollo_attempted_utc"
APOLLO_STATUS_COL = "apollo_status"
APOLLO_TOP_NAME_COL = "apollo_top_name"
APOLLO_TOP_TITLE_COL = "apollo_top_title"
APOLLO_TOP_EMAIL_COL = "apollo_top_email"
APOLLO_LI_COL = "apollo_linkedin_url"


def domain_of(url: str) -> str:
    if not url:
        return ""
    host = urlparse(url if "://" in url else "https://" + url).netloc
    return (host or "").lower().lstrip("www.").split("/", 1)[0]


def has_named_email(r: dict) -> bool:
    return bool((r.get("emails_on_site") or "").strip()
                or (r.get("ir_email") or "").strip())


def has_generic_email(r: dict) -> bool:
    return bool((r.get("generic_emails_on_site") or "").strip())


def is_bd(r: dict) -> bool:
    return (r.get("illiquid_book_likelihood") or "").lower() in ("high", "medium")


def wa(r: dict) -> str:
    return (r.get("website_accuracy") or "").strip().lower()


def in_scope(r: dict, scope: str) -> bool:
    if not is_bd(r) or not (r.get("website_url") or "").strip():
        return False
    if scope == "verified-bd":
        return wa(r) == "verified"
    if scope == "probable-bd":
        return wa(r) in ("verified", "probable")
    if scope == "hunter-zero":
        return wa(r) in ("verified", "probable") and not has_named_email(r)
    if scope == "only-generic":
        return wa(r) in ("verified", "probable") and not has_named_email(r) and has_generic_email(r)
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


def _atomic_write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    ordered = canonical_fieldnames(fieldnames)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ordered)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in ordered})
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["verified-bd", "probable-bd",
                                        "hunter-zero", "only-generic"],
                    default="verified-bd")
    ap.add_argument("--cerefs", default="",
                    help="explicit cerefs to target; overrides --scope")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="re-query Apollo even if apollo_attempted_utc is set")
    ap.add_argument("--titles", default="",
                    help="comma-separated titles to search (default: CFO/Head of Valuation/etc.)")
    ap.add_argument("--linkedin-export", default=str(LI_EXPORT_DEFAULT),
                    help="CSV path for the LinkedIn worklist of discovered people")
    args = ap.parse_args()

    titles = [t.strip() for t in args.titles.split(",") if t.strip()] \
        or list(apollo_io.DEFAULT_DECISION_MAKER_TITLES)

    # Health-check first so a misconfigured key fails loudly before we read CSVs.
    ok, reason = apollo_io.health_check()
    print(f"[apollo] key configured: {bool(apollo_io.API_KEY)}  health: {ok} ({reason})",
          file=sys.stderr)
    if apollo_io.API_KEY and not ok and not args.dry_run:
        print("[apollo] proceeding anyway; per-firm status will record actual API state",
              file=sys.stderr)

    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig")))
    if not rows:
        print("empty CSV", file=sys.stderr); sys.exit(1)
    fieldnames = list(rows[0].keys())
    for col in (APOLLO_AT_COL, APOLLO_STATUS_COL, APOLLO_TOP_NAME_COL,
                APOLLO_TOP_TITLE_COL, APOLLO_TOP_EMAIL_COL, APOLLO_LI_COL):
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
        candidates = [r for r in candidates if not (r.get(APOLLO_AT_COL) or "").strip()]

    print(f"scope={args.scope} candidates={len(candidates)}", file=sys.stderr)
    if args.limit:
        candidates = candidates[:args.limit]
        print(f"  limited to {len(candidates)}", file=sys.stderr)

    if args.dry_run:
        for r in candidates[:30]:
            print(f"  {r['ceref']:8s} {r['name_en'][:40]:42s} {r['website_url'][:55]}")
        if len(candidates) > 30:
            print(f"  ... +{len(candidates) - 30} more")
        return

    li_rows = []
    hits = 0
    no_match = 0
    not_configured = 0
    errors = 0
    for i, r in enumerate(candidates, 1):
        ceref = r["ceref"]
        d = domain_of(r["website_url"])
        if not d:
            r[APOLLO_AT_COL] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
            r[APOLLO_STATUS_COL] = "no_domain"
            continue
        print(f"[{i}/{len(candidates)}] {ceref}  {d}", file=sys.stderr)
        rec = apollo_io.find_people(d, titles=titles, per_page=5)
        if not rec:
            errors += 1; continue
        r[APOLLO_AT_COL] = rec["fetched_at_utc"]
        r[APOLLO_STATUS_COL] = rec["status"]
        status = rec["status"]
        if status == apollo_io.STATUS_NOT_CONFIGURED:
            not_configured += 1
        elif status in (apollo_io.STATUS_QUOTA_EXHAUSTED,
                        apollo_io.STATUS_QUOTA_UNKNOWN):
            print(f"  quota: {status} — stopping batch", file=sys.stderr)
            break
        elif status == apollo_io.STATUS_OK and rec.get("people"):
            people = rec["people"]
            top = people[0]
            r[APOLLO_TOP_NAME_COL] = top["name"]
            r[APOLLO_TOP_TITLE_COL] = top["title"]
            r[APOLLO_TOP_EMAIL_COL] = top.get("email") or ""
            r[APOLLO_LI_COL] = top.get("linkedin_url") or ""
            hits += 1
            print(f"  + {top['name']} · {top['title']}", file=sys.stderr)
            # Persist all matches into the LinkedIn worklist (caller may have
            # different person for outreach than the top match)
            for p in people:
                li_rows.append({
                    "ceref": ceref, "firm": r["name_en"], "domain": d,
                    **p,
                })
        elif status == apollo_io.STATUS_NO_MATCH:
            no_match += 1
        elif status == apollo_io.STATUS_ERROR:
            errors += 1

        if i % 10 == 0:
            _atomic_write_csv(CSV_PATH, fieldnames, rows)

    _atomic_write_csv(CSV_PATH, fieldnames, rows)

    # Write LinkedIn worklist
    if li_rows:
        cols = ["ceref", "firm", "domain", "name", "title", "email",
                "email_status", "linkedin_url", "city", "country"]
        export_path = Path(args.linkedin_export)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with export_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for row in li_rows:
                w.writerow({k: row.get(k, "") for k in cols})
        print(f"linkedin worklist: wrote {len(li_rows)} rows to {export_path}",
              file=sys.stderr)

    print(f"\nprocessed:        {len(candidates)}", file=sys.stderr)
    print(f"  hits:           {hits}", file=sys.stderr)
    print(f"  no_match:       {no_match}", file=sys.stderr)
    print(f"  not_configured: {not_configured}", file=sys.stderr)
    print(f"  errors:         {errors}", file=sys.stderr)


if __name__ == "__main__":
    main()
