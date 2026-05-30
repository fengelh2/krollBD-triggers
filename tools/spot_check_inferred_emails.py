"""Spot-check the accuracy of the 959 inferred-pattern emails.

Samples N random emails from data/inferred_named_emails.csv (stratified by
website_accuracy tier so each tier gets a fair share) and runs each through
email_verifier.verify(). Reports a real per-tier accuracy rate.

This is the empirical ground truth the dashboard's "medium confidence" label
has been hand-waving about.

Usage:
  python tools/spot_check_inferred_emails.py --n 30
  python tools/spot_check_inferred_emails.py --n 60 --strata verified,probable
  python tools/spot_check_inferred_emails.py --dry-run   # show plan, no API
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import email_verifier  # noqa: E402

CSV_PATH = PROJECT_ROOT / "data" / "strategy_classification.csv"
WORKLIST_PATH = PROJECT_ROOT / "data" / "inferred_named_emails.csv"
REPORT_PATH = PROJECT_ROOT / "data" / "spot_check_results.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="total sample size")
    ap.add_argument("--strata", default="verified,probable",
                    help="comma-separated wa tiers to sample (default verified+probable)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Build wa lookup
    cls_rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig")))
    wa_by_ceref = {r["ceref"]: (r.get("website_accuracy") or "").strip().lower()
                   for r in cls_rows}

    inferred = list(csv.DictReader(WORKLIST_PATH.open(encoding="utf-8-sig")))
    # Stratify
    strata = [s.strip() for s in args.strata.split(",") if s.strip()]
    per_stratum = max(1, args.n // len(strata))
    sample: list[dict] = []
    rng = random.Random(20260530)
    for tier in strata:
        pool = [r for r in inferred if wa_by_ceref.get(r["ceref"], "") == tier]
        if not pool:
            print(f"  [warn] stratum '{tier}': 0 inferred emails in pool", file=sys.stderr)
            continue
        rng.shuffle(pool)
        sample.extend(pool[:per_stratum])

    print(f"sample size: {len(sample)} (target {args.n}, stratified by {strata})",
          file=sys.stderr)

    if args.dry_run:
        print("\n-- sample (would verify) --", file=sys.stderr)
        for r in sample[:30]:
            print(f"  {r['ceref']:8s} {wa_by_ceref.get(r['ceref'],''):9s} {r['firm'][:32]:34s} {r['ro_name'][:24]:26s} -> {r['inferred_email']}",
                  file=sys.stderr)
        return

    # Run verifier
    results = []
    for i, r in enumerate(sample, 1):
        em = r["inferred_email"]
        print(f"[{i}/{len(sample)}] {em}", file=sys.stderr)
        rec = email_verifier.verify(em)
        status = (rec or {}).get("status", "error")
        results.append({
            "ceref": r["ceref"],
            "firm": r["firm"],
            "ro_name": r["ro_name"],
            "wa_tier": wa_by_ceref.get(r["ceref"], ""),
            "pattern": r["pattern"],
            "email": em,
            "status": status,
            "is_smtp_valid": (rec or {}).get("is_smtp_valid", ""),
            "is_catch_all": (rec or {}).get("is_catch_all", ""),
            "quality_score": (rec or {}).get("quality_score", ""),
        })
        if status == email_verifier.STATUS_QUOTA_EXHAUSTED:
            print("  → quota exhausted, stopping batch", file=sys.stderr)
            break
        if status == email_verifier.STATUS_NOT_CONFIGURED:
            print("  → key not configured, stopping batch", file=sys.stderr)
            print("  set ABSTRACTAPI_KEY in .env (sign up: abstractapi.com)",
                  file=sys.stderr)
            break

    # Write report
    cols = ["ceref", "firm", "ro_name", "wa_tier", "pattern", "email",
            "status", "is_smtp_valid", "is_catch_all", "quality_score"]
    with REPORT_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in results:
            w.writerow({k: row.get(k, "") for k in cols})

    # Honest tally
    print("\n=== results ===", file=sys.stderr)
    overall = Counter(r["status"] for r in results)
    print(f"  overall ({len(results)}): {dict(overall)}", file=sys.stderr)
    for tier in strata:
        tier_results = [r for r in results if r["wa_tier"] == tier]
        if not tier_results:
            continue
        c = Counter(r["status"] for r in tier_results)
        deliv = c.get(email_verifier.STATUS_DELIVERABLE, 0)
        risky = c.get(email_verifier.STATUS_RISKY, 0)
        undeliv = c.get(email_verifier.STATUS_UNDELIVERABLE, 0)
        unknown = c.get(email_verifier.STATUS_UNKNOWN, 0)
        total = deliv + risky + undeliv + unknown
        if total:
            print(f"  {tier:9s} (n={total}): "
                  f"{100*deliv//total}% deliverable · "
                  f"{100*risky//total}% risky · "
                  f"{100*undeliv//total}% undeliverable · "
                  f"{100*unknown//total}% unknown",
                  file=sys.stderr)
    print(f"\n→ wrote {REPORT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
