"""Run Hunter.io /email-finder for the primary RO of every firm in a cohort.

Use case: deep-scrape often returns generic emails (info@ / contact@). To
upgrade those firms to a NAMED contact, we'd normally wait until a trigger
fires — but for ad-hoc backfills (e.g. "find named emails for all 45 generic-
only firms") this tool runs the cascade Layer 4 step in bulk.

Quota-aware: the underlying hunter_io.find_email() refuses to call when
remaining quota < QUOTA_FLOOR (5) and writes results to the JSON cache so
re-runs are free.

Reads:
  - data/strategy_classification.csv  → for cohort filtering + firm domain
  - data/snapshots/sfc_t9_corp_ros_latest.csv → for primary RO names

Writes (atomic):
  - data/strategy_classification.csv  → adds hunter_email + hunter_score
    columns next to ir_email; never overwrites a populated value (merge-only)
  - data/hunter_io_cache.json         → updated via hunter_io.find_email

Usage:
  python tools/hunter_for_cohort.py --scope only_generic
  python tools/hunter_for_cohort.py --scope hunter_zero --limit 10
  python tools/hunter_for_cohort.py --cerefs ANA553,BUS898 --force
  python tools/hunter_for_cohort.py --scope only_generic --dry-run
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))

import hunter_io  # noqa: E402

CSV_PATH = PROJECT_ROOT / "data" / "strategy_classification.csv"
PAIRS_PATH = PROJECT_ROOT / "data" / "snapshots" / "sfc_t9_corp_ros_latest.csv"

HUNTER_EMAIL_COL = "hunter_email"
HUNTER_SCORE_COL = "hunter_score"
HUNTER_STATUS_COL = "hunter_status"
HUNTER_AT_COL = "hunter_attempted_utc"


def domain_of(url: str) -> str:
    if not url:
        return ""
    host = urlparse(url if "://" in url else "https://" + url).netloc
    return (host or "").lower().lstrip("www.").split("/", 1)[0]


def has_named_email(r: dict) -> bool:
    return bool((r.get("emails_on_site") or "").strip() or (r.get("ir_email") or "").strip())


def has_generic_email(r: dict) -> bool:
    return bool((r.get("generic_emails_on_site") or "").strip())


def is_bd(r: dict) -> bool:
    return (r.get("illiquid_book_likelihood") or "").lower() in ("high", "medium")


def wa(r: dict) -> str:
    return (r.get("website_accuracy") or "").strip().lower()


def in_scope(r: dict, scope: str) -> bool:
    if not is_bd(r):
        return False
    if not (r.get("website_url") or "").strip():
        return False
    if scope == "only_generic":
        # BD + verified/probable site + no named email + has generic email
        return wa(r) in ("verified", "probable") and not has_named_email(r) and has_generic_email(r)
    if scope == "hunter_zero":
        # BD + verified/probable site + no named email + no generic email
        return wa(r) in ("verified", "probable") and not has_named_email(r) and not has_generic_email(r)
    if scope == "hunter_all":
        # entire Hunter cohort (zero + generic)
        return wa(r) in ("verified", "probable") and not has_named_email(r)
    raise SystemExit(f"unknown --scope {scope!r}")


def primary_ro_for_corp(pairs: list[dict], ceref: str) -> dict | None:
    for p in pairs:
        if p.get("corp_ceref") == ceref:
            return p
    return None


def _atomic_write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["only_generic", "hunter_zero", "hunter_all"],
                    default="only_generic",
                    help="which cohort to target")
    ap.add_argument("--cerefs", default="",
                    help="comma-separated cerefs; overrides --scope")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="re-query Hunter even if hunter_attempted_utc is set")
    ap.add_argument("--use-all-credits", action="store_true",
                    help="lower QUOTA_FLOOR to 0 for this batch (burn the last few credits)")
    ap.add_argument("--linkedin-export", default="",
                    help="path to write a LinkedIn-target CSV for firms NOT processed "
                         "(e.g. quota-exhausted) — firm + ro name + domain")
    args = ap.parse_args()

    if args.use_all_credits:
        hunter_io.QUOTA_FLOOR = 0
        print("[!] QUOTA_FLOOR lowered to 0 for this run — will burn final credits", file=sys.stderr)

    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig")))
    if not rows:
        print("empty CSV", file=sys.stderr); sys.exit(1)
    fieldnames = list(rows[0].keys())
    for col in (HUNTER_EMAIL_COL, HUNTER_SCORE_COL, HUNTER_STATUS_COL, HUNTER_AT_COL):
        if col not in fieldnames:
            fieldnames.append(col)
            for r in rows:
                r[col] = ""

    pairs = list(csv.DictReader(PAIRS_PATH.open(encoding="utf-8-sig")))

    target_cerefs = {c.strip() for c in args.cerefs.split(",") if c.strip()}
    if target_cerefs:
        candidates = [r for r in rows if r["ceref"] in target_cerefs]
    else:
        candidates = [r for r in rows if in_scope(r, args.scope)]

    if not args.force:
        candidates = [r for r in candidates if not (r.get(HUNTER_AT_COL) or "").strip()]

    print(f"scope={args.scope} candidates={len(candidates)}", file=sys.stderr)
    if args.limit:
        candidates = candidates[:args.limit]
        print(f"  limited to {len(candidates)}", file=sys.stderr)

    qr = hunter_io.remaining_quota()
    print(f"hunter quota remaining: {qr} (floor={hunter_io.QUOTA_FLOOR})", file=sys.stderr)

    if args.dry_run:
        for r in candidates[:30]:
            ro = primary_ro_for_corp(pairs, r["ceref"])
            print(f"  {r['ceref']:8s}  {r['name_en'][:36]:38s}  ro={ro['ro_full_name'] if ro else '(none)'}")
        if len(candidates) > 30:
            print(f"  ... +{len(candidates) - 30} more")
        return

    hits = 0
    no_match = 0
    no_ro = 0
    skipped_quota = 0
    errors = 0
    for i, r in enumerate(candidates, 1):
        ceref = r["ceref"]
        ro = primary_ro_for_corp(pairs, ceref)
        if not ro:
            print(f"[{i}/{len(candidates)}] {ceref}  (no RO) — skip", file=sys.stderr)
            no_ro += 1
            r[HUNTER_AT_COL] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
            r[HUNTER_STATUS_COL] = "no_ro"
            continue
        first = (ro.get("ro_first_full") or ro.get("ro_first_short") or "").strip()
        last = (ro.get("ro_last") or "").strip()
        d = domain_of(r["website_url"])
        print(f"[{i}/{len(candidates)}] {ceref}  {first} {last} @ {d}", file=sys.stderr)
        rec = hunter_io.find_email(d, first, last)
        if not rec:
            errors += 1; continue
        status = rec.get("status") or "unknown"
        r[HUNTER_AT_COL] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        r[HUNTER_STATUS_COL] = status
        if rec.get("email"):
            r[HUNTER_EMAIL_COL] = rec["email"]
            r[HUNTER_SCORE_COL] = str(rec.get("score") or "")
            hits += 1
            print(f"  + {rec['email']}  score={rec.get('score')}", file=sys.stderr)
        elif status == "no_match":
            no_match += 1
        elif status in (hunter_io.STATUS_QUOTA_EXHAUSTED, hunter_io.STATUS_QUOTA_UNKNOWN):
            skipped_quota += 1
            print(f"  quota exhausted ({status}) — stopping batch", file=sys.stderr)
            break
        else:
            errors += 1

        if i % 5 == 0:
            _atomic_write_csv(CSV_PATH, fieldnames, rows)

    _atomic_write_csv(CSV_PATH, fieldnames, rows)

    # Emit a LinkedIn-target CSV for candidates we couldn't process (quota,
    # no_ro, etc.). Useful as a manual follow-up worklist.
    if args.linkedin_export:
        skipped = [r for r in candidates if (r.get(HUNTER_STATUS_COL) or "") in
                   ("", "quota_exhausted", "quota_unknown")]
        out_path = Path(args.linkedin_export)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ceref", "firm", "ro_name", "domain", "linkedin_search_url"])
            for r in skipped:
                ro = primary_ro_for_corp(pairs, r["ceref"])
                ro_name = (ro or {}).get("ro_full_name", "")
                d = domain_of(r["website_url"])
                q = f"{ro_name} {r['name_en']}".replace(" ", "+")
                w.writerow([r["ceref"], r["name_en"], ro_name, d,
                            f"https://www.linkedin.com/search/results/people/?keywords={q}"])
        print(f"linkedin-export: wrote {len(skipped)} unprocessed firms → {out_path}", file=sys.stderr)

    print("", file=sys.stderr)
    print(f"processed:    {len(candidates)}", file=sys.stderr)
    print(f"  hits:       {hits}", file=sys.stderr)
    print(f"  no_match:   {no_match}", file=sys.stderr)
    print(f"  no_ro:      {no_ro}", file=sys.stderr)
    print(f"  errors:     {errors}", file=sys.stderr)
    print(f"  skipped_q:  {skipped_quota}", file=sys.stderr)
    qr2 = hunter_io.remaining_quota()
    print(f"hunter quota now: {qr2}", file=sys.stderr)


if __name__ == "__main__":
    main()
