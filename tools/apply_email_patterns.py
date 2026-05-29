"""Apply discovered email patterns to every RO of every firm.

After tools/serpapi_email_patterns.py populates `email_pattern_inferred`
for each firm, this tool reads each firm's primary ROs from the SFC pairs
snapshot and constructs CANDIDATE named emails using the pattern.

These are GUESSED emails (high-confidence pattern, but not Hunter-verified).
They go into a separate column + worklist CSV — never overwriting
emails_on_site or generic_emails_on_site.

Reads:
  - data/strategy_classification.csv  (firms with email_pattern_inferred)
  - data/snapshots/sfc_t9_corp_ros_latest.csv

Writes:
  - data/strategy_classification.csv  (adds inferred_named_emails column,
    comma-separated list of constructed addresses. Atomic + canonical order.)
  - data/inferred_named_emails.csv  (worklist: ceref, firm, ro_name, email,
    pattern, evidence)

Usage:
  python tools/apply_email_patterns.py --dry-run
  python tools/apply_email_patterns.py
  python tools/apply_email_patterns.py --max-ros-per-firm 5
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from classify_strategy import canonical_fieldnames  # noqa: E402

CSV_PATH = PROJECT_ROOT / "data" / "strategy_classification.csv"
PAIRS_PATH = PROJECT_ROOT / "data" / "snapshots" / "sfc_t9_corp_ros_latest.csv"
WORKLIST_PATH = PROJECT_ROOT / "data" / "inferred_named_emails.csv"

PATTERN_COL = "email_pattern_inferred"
INFERRED_COL = "inferred_named_emails"


def domain_of(url: str) -> str:
    if not url:
        return ""
    host = urlparse(url if "://" in url else "https://" + url).netloc
    return (host or "").lower().lstrip("www.").split("/", 1)[0]


# ---------------- name normalization ----------------

def _normalize_part(s: str) -> str:
    """Lowercase, strip non-letter chars. 'O'Brien' → 'obrien'."""
    s = (s or "").lower()
    # ASCII-fold common HK/CN diacritics aren't a concern for SFC names which
    # are romanized; we just strip non-letters.
    return re.sub(r"[^a-z]", "", s)


def first_last_from_ro(p: dict) -> tuple[str, str]:
    """Extract (first, last) tokens, ready for pattern application.
    Strategy for first name: prefer ro_first_short (just the given name);
    if missing, take the FIRST word of ro_first_full. Drop middle names —
    'Colin Dennis Banfield' should build 'colin.banfield', not
    'colindennis.banfield', because that's the actual corporate convention."""
    short = (p.get("ro_first_short") or "").strip()
    if not short:
        # Take only the first word of the full first-name field
        full = (p.get("ro_first_full") or "").strip()
        short = full.split()[0] if full else ""
    first = _normalize_part(short)
    last = _normalize_part(p.get("ro_last") or "")
    return first, last


# ---------------- pattern application ----------------

def apply_pattern(pattern: str, first: str, last: str) -> str:
    """Apply pattern tokens to first/last names. Returns the local part
    (before @). Returns '' if any required token is empty."""
    p = pattern or ""
    if not (first and last):
        return ""
    out = p
    out = out.replace("{first}", first)
    out = out.replace("{last}", last)
    out = out.replace("{f}", first[:1])
    out = out.replace("{l}", last[:1])
    # Sanity: only letters, dots, hyphens, underscores in local part
    if not re.fullmatch(r"[a-z0-9._\-]+", out):
        return ""
    return out


# ---------------- I/O ----------------

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-ros-per-firm", type=int, default=10,
                    help="cap on ROs per firm to avoid runaway lists")
    args = ap.parse_args()

    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig")))
    if not rows:
        print("empty CSV", file=sys.stderr); sys.exit(1)
    fieldnames = list(rows[0].keys())
    if INFERRED_COL not in fieldnames:
        fieldnames.append(INFERRED_COL)
        for r in rows:
            r[INFERRED_COL] = ""

    pairs = list(csv.DictReader(PAIRS_PATH.open(encoding="utf-8-sig")))
    ros_by_corp: dict[str, list[dict]] = {}
    for p in pairs:
        ros_by_corp.setdefault(p["corp_ceref"], []).append(p)

    with_pattern = [r for r in rows if (r.get(PATTERN_COL) or "").strip()]
    print(f"firms with pattern: {len(with_pattern)}", file=sys.stderr)

    worklist: list[dict] = []
    firms_with_emails = 0
    total_emails = 0
    for r in with_pattern:
        ceref = r["ceref"]
        pattern = r[PATTERN_COL]
        d = domain_of(r.get("website_url"))
        if not d:
            continue
        firm_ros = ros_by_corp.get(ceref, [])[:args.max_ros_per_firm]
        if not firm_ros:
            continue
        constructed: list[str] = []
        for ro in firm_ros:
            first, last = first_last_from_ro(ro)
            local = apply_pattern(pattern, first, last)
            if not local:
                continue
            em = f"{local}@{d}".lower()
            constructed.append(em)
            worklist.append({
                "ceref": ceref,
                "firm": r["name_en"],
                "ro_name": ro.get("ro_full_name", ""),
                "ro_ceref": ro.get("ro_ceref", ""),
                "domain": d,
                "pattern": pattern,
                "inferred_email": em,
            })
        if constructed:
            firms_with_emails += 1
            total_emails += len(constructed)
            r[INFERRED_COL] = ",".join(sorted(set(constructed)))

    print(f"firms with at least one inferred email: {firms_with_emails}", file=sys.stderr)
    print(f"total inferred emails:                  {total_emails}", file=sys.stderr)

    if args.dry_run:
        print("\n-- sample --", file=sys.stderr)
        for w in worklist[:15]:
            print(f"  {w['ceref']:8s} {w['firm'][:34]:36s} {w['ro_name'][:22]:24s} → {w['inferred_email']}",
                  file=sys.stderr)
        return

    _atomic_write(rows, fieldnames)
    print(f"updated {CSV_PATH}", file=sys.stderr)

    cols = ["ceref", "firm", "domain", "ro_name", "ro_ceref", "pattern", "inferred_email"]
    WORKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with WORKLIST_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in worklist:
            w.writerow({k: row.get(k, "") for k in cols})
    print(f"worklist: {len(worklist)} rows → {WORKLIST_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
