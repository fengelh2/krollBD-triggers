"""Post-process: add a derived `website_accuracy` column to
strategy_classification.csv combining three existing signals into one verdict.

Rules:
    verified   = LLM said active + multi-source agreement + name word match
    probable   = LLM said active, content rich enough, firm name found on page
    suspect    = ambiguous name match + thin content
    unverified = placeholder_site (right firm but no info to verify)
    not_found  = no website at all

Idempotent — re-running just recomputes the column.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify_strategy import canonical_fieldnames  # noqa: E402

CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "strategy_classification.csv"


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


STRONG_EVIDENCE = {"multiple_pages_corroborate", "one_clear_statement"}


def verdict(r: dict) -> str:
    op = (r.get("operational_status") or "").strip()
    ev = (r.get("evidence_strength") or "").strip()
    dis = (r.get("name_disambiguation_status") or "").strip()
    site = (r.get("website_url") or "").strip()

    if not site or op == "no_site_found":
        return "not_found"
    if op == "placeholder_site":
        if dis == "ambiguous":
            return "suspect"
        return "unverified"
    if op == "dormant_signals":
        return "suspect"
    # operational_status = active or other
    if dis == "ambiguous":
        return "suspect"
    if dis == "no_match":
        return "suspect"
    if op == "active" and ev in STRONG_EVIDENCE and dis in ("high_confidence",):
        return "verified"
    if op == "active" and dis in ("high_confidence", "medium_confidence"):
        return "probable"
    return "unverified"


def main() -> None:
    with CSV_PATH.open(encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        fieldnames = list(rdr.fieldnames or [])
        rows = list(rdr)

    if "website_accuracy" not in fieldnames:
        fieldnames.append("website_accuracy")

    from collections import Counter
    cnt = Counter()
    for r in rows:
        v = verdict(r)
        r["website_accuracy"] = v
        cnt[v] += 1

    print("== website_accuracy distribution ==", file=sys.stderr)
    for k in ("verified", "probable", "unverified", "suspect", "not_found"):
        v = cnt.get(k, 0)
        print(f"  {k:12s} {v:5d}  ({v*100//len(rows):>3}%)", file=sys.stderr)

    _atomic_write_csv(CSV_PATH, canonical_fieldnames(fieldnames), rows)
    print(f"\n→ wrote {CSV_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
