"""Post-process pass: clear extracted emails for firms whose website didn't
verify as actually belonging to the named firm.

Why: classify_strategy.py extracts emails from the scraped firm-page markdown
in the same pass as everything else. If the website turned out to be the wrong
firm (e.g. MFS HK → hkifa.org.hk because SerpAPI surfaced the industry-body page),
the extracted emails are wrong too.

We already have a signal: `name_disambiguation_status` in
strategy_classification.csv:
  high_confidence    — page contains 2+ words from the firm's name
  medium_confidence  — page contains 1 name word
  ambiguous          — page exists but firm name not found
  no_match           — no firm-name presence at all

This script clears `emails_on_site` and `generic_emails_on_site` for any row
where disambiguation is `ambiguous` or `no_match`, and records the cleanup in a
new column `email_extraction_cleared` so we know it was deliberate.

Run after bulk classifier completes. Idempotent — re-running on already-cleaned
data does nothing.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "data" / "strategy_classification.csv"


def _atomic_write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    """Tmp + fsync + atomic replace. Prevents partial-write data loss on Ctrl+C / OOM."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)

# Multiple signals decide whether to keep extracted emails:
#  - name_disambiguation_status (word-match heuristic; weak — gets fooled by
#    generic words like "Hong Kong" appearing in industry-body pages)
#  - operational_status (LLM's verdict on whether the site exists / is the firm)
#  - evidence_strength (LLM's own confidence in its findings)
# Most reliable: LLM-side signals. Clear when LLM itself said no_evidence /
# no_site_found / placeholder regardless of the disambiguation word-match.
UNVERIFIED_STATUSES = {"no_match"}  # legacy disambig signal
UNVERIFIED_OP_STATUSES = {"no_site_found", "unknown"}
UNVERIFIED_EVIDENCE = {"no_evidence", "guessed"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be cleared without modifying the file.")
    args = ap.parse_args()

    if not CSV_PATH.exists():
        raise SystemExit(f"Not found: {CSV_PATH}")

    with CSV_PATH.open(encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        fieldnames = list(rdr.fieldnames or [])
        rows = list(rdr)

    # Add the audit column if missing
    if "email_extraction_cleared" not in fieldnames:
        fieldnames.append("email_extraction_cleared")
        for r in rows:
            r["email_extraction_cleared"] = ""

    cleared_count = 0
    stats = {"no_match": 0, "ambiguous": 0, "kept_high": 0, "kept_medium": 0, "other": 0}

    import re as _re
    NOISE_WORDS = {"hong", "kong", "asia", "asian", "limited", "ltd", "group",
                   "the", "capital", "management", "investment", "investments",
                   "company", "co", "advisors", "advisers", "partners",
                   "holdings", "of", "and", "securities", "fund", "funds",
                   "international", "global", "wealth", "asset", "advisory",
                   "services", "trading", "futures", "research", "private"}

    def email_domain_matches_firm(emails: str, firm_name: str) -> bool:
        """True if any email's domain contains a significant word from the firm name.
        Uses words of any length (e.g. 'kai', 'yin' for 'Kai Yin Securities')."""
        if not emails:
            return True
        words = [w.lower() for w in _re.split(r"[^a-z]+", firm_name.lower()) if len(w) >= 2]
        sig = [w for w in words if w not in NOISE_WORDS]
        if not sig:
            return True  # can't judge — keep
        for em in emails.split(","):
            dom = em.split("@", 1)[-1].lower().strip().replace(".", "")
            if any(w in dom for w in sig):
                return True
        return False

    for r in rows:
        disambig = (r.get("name_disambiguation_status") or "").strip()
        op = (r.get("operational_status") or "").strip()
        evidence = (r.get("evidence_strength") or "").strip()
        emails_combined = ",".join(filter(None, [r.get("emails_on_site",""), r.get("generic_emails_on_site","")]))
        had_emails = bool(emails_combined)

        # Reasons to NOT trust extracted emails:
        llm_uncertain = (disambig in UNVERIFIED_STATUSES
                         or op in UNVERIFIED_OP_STATUSES
                         or evidence in UNVERIFIED_EVIDENCE)
        # Only clear when LLM is uncertain AND domain doesn't match firm name —
        # catches MFS→hkifa cases without nuking legit captures like Maunakai→maunakaicapital.com
        bad = llm_uncertain and not email_domain_matches_firm(emails_combined, r.get("name_en",""))

        if bad:
            stats[disambig if disambig in stats else "other"] = stats.get(
                disambig if disambig in stats else "other", 0) + 1
            if had_emails and r.get("email_extraction_cleared") != "true":
                if cleared_count < 15:
                    print(f"  CLEAR {r['ceref']} {r['name_en'][:38]:38s} "
                          f"disambig={disambig:18s} op={op:15s} ev={evidence:25s} "
                          f"→ {(r.get('emails_on_site') or r.get('generic_emails_on_site'))[:50]}",
                          file=sys.stderr)
                cleared_count += 1
                if not args.dry_run:
                    r["emails_on_site"] = ""
                    r["generic_emails_on_site"] = ""
                    r["email_extraction_cleared"] = "true"
        elif disambig == "high_confidence":
            stats["kept_high"] += 1
        elif disambig == "medium_confidence":
            stats["kept_medium"] += 1
        else:
            stats["other"] += 1

    print(file=sys.stderr)
    print(f"== Disambiguation distribution ({len(rows)} rows) ==", file=sys.stderr)
    for k, v in stats.items():
        print(f"  {k:14s}  {v}", file=sys.stderr)
    print(f"\n→ Would clear emails on {cleared_count} firms" if args.dry_run else
          f"\n→ Cleared emails on {cleared_count} firms", file=sys.stderr)

    if args.dry_run:
        return

    _atomic_write_csv(CSV_PATH, fieldnames, rows)
    print(f"Updated {CSV_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
