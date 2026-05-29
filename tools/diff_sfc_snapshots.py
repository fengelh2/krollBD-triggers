"""Diff two SFC register snapshots and emit a markdown changelog.

Compares the two most recent dated snapshots (or two explicit dates) for each of:
  - corps        (added / removed / address-changed / name-changed)
  - individuals  (added / removed)
  - corp_ros     (RO joined firm / RO left firm)

Output: projects/krollBD/.tmp/sfc_diff_<NEW>_vs_<OLD>.md
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # projects/krollBD/
SNAP_DIR = PROJECT_ROOT / "data" / "snapshots"
OUT_DIR = PROJECT_ROOT / ".tmp"

def latest_two(prefix: str, ratype: str) -> tuple[Path, Path]:
    """Return (latest_file, prev_file). Both required for diffing."""
    latest = SNAP_DIR / f"sfc_t{ratype}_{prefix}_latest.csv"
    prev = SNAP_DIR / f"sfc_t{ratype}_{prefix}_prev.csv"
    if not latest.exists():
        raise SystemExit(f"Missing {latest.name} — run scrape_sfc_register.py first.")
    if not prev.exists():
        raise SystemExit(
            f"Missing {prev.name} — need two scrapes for a diff. "
            "Run scrape_sfc_register.py twice (with at least one rotation between)."
        )
    return latest, prev


def read_csv(p: Path) -> list[dict]:
    with p.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def diff_by_key(new: list[dict], old: list[dict], key: str) -> tuple[list, list, list[dict]]:
    """Return (added, removed, changed) — changed = rows where any non-key field differs."""
    nmap = {r[key]: r for r in new}
    omap = {r[key]: r for r in old}
    added = [nmap[k] for k in nmap.keys() - omap.keys()]
    removed = [omap[k] for k in omap.keys() - nmap.keys()]
    changed = []
    for k in nmap.keys() & omap.keys():
        diffs = {f: (omap[k].get(f), nmap[k].get(f))
                 for f in nmap[k] if nmap[k].get(f) != omap[k].get(f)}
        if diffs:
            changed.append({"key": k, "name": nmap[k].get("name_en") or nmap[k].get("corp_name", ""), "diffs": diffs})
    return added, removed, changed


def diff_ros(new: list[dict], old: list[dict]):
    """RO joined / left a firm, keyed on (corp_ceref, ro_ceref)."""
    def key(r): return (r["corp_ceref"], r["ro_ceref"])
    nmap = {key(r): r for r in new}
    omap = {key(r): r for r in old}
    joined = [nmap[k] for k in nmap.keys() - omap.keys()]
    left = [omap[k] for k in omap.keys() - nmap.keys()]
    return joined, left


def fmt_md(new_date: str, old_date: str, sections: dict) -> str:
    lines = [
        f"# SFC Type 9 register diff — {new_date} vs {old_date}",
        "",
        "Source: SFC public register snapshots in `projects/krollBD/data/snapshots/`.",
        "",
    ]

    cadd, crem, cchg = sections["corps"]
    # Split "changed" into license-status flips vs other (address/name) edits
    status_flips = [c for c in cchg if "has_active_licence" in c["diffs"]]
    other_edits = [c for c in cchg if "has_active_licence" not in c["diffs"]]
    lines.append(f"## Corporations  (+{len(cadd)} brand-new, −{len(crem)} dropped from register, ⚑{len(status_flips)} status flip, ~{len(other_edits)} other edits)")
    if cadd:
        lines.append("\n### BRAND-NEW Type 9 corporations (first appearance in register)  ← BD priority\n")
        for c in sorted(cadd, key=lambda r: r["name_en"]):
            lines.append(f"- **{c['name_en']}**  (`{c['ceref']}`) — active=`{c['has_active_licence']}` — {c['address']}")
    if status_flips:
        lines.append("\n### License-status flips  ← act on `Y→N` (retirement) and `N→Y` (reactivation)\n")
        for ch in status_flips:
            o, n = ch["diffs"]["has_active_licence"]
            arrow = f"{o} → {n}"
            tag = "RETIRED" if (o, n) == ("Y", "N") else ("REACTIVATED" if (o, n) == ("N", "Y") else "changed")
            lines.append(f"- **{tag}**: {ch['name']}  (`{ch['key']}`) — has_active_licence {arrow}")
    if crem:
        lines.append("\n### Removed from register entirely (rare — usually a CE-ref change)\n")
        for c in sorted(crem, key=lambda r: r["name_en"]):
            lines.append(f"- {c['name_en']}  (`{c['ceref']}`)")
    if other_edits:
        lines.append("\n### Other edits (name / address / AMLO / deemed)\n")
        for ch in other_edits:
            for fld, (o, n) in ch["diffs"].items():
                lines.append(f"- `{ch['key']}` {ch['name']} — **{fld}**: `{o}` → `{n}`")

    iadd, irem, _ = sections["individuals"]
    lines.append(f"\n## Individuals  (+{len(iadd)} newly licensed, −{len(irem)} no longer licensed)")
    if iadd:
        lines.append("\n### Newly Type 9-licensed individuals (sample, first 50)\n")
        for ind in sorted(iadd, key=lambda r: r["name_en"])[:50]:
            lines.append(f"- {ind['name_en']}  (`{ind['ceref']}`)")
        if len(iadd) > 50:
            lines.append(f"- ... and {len(iadd) - 50} more")
    if irem:
        lines.append("\n### Individuals no longer Type 9-licensed (sample, first 50)\n")
        for ind in sorted(irem, key=lambda r: r["name_en"])[:50]:
            lines.append(f"- {ind['name_en']}  (`{ind['ceref']}`)")
        if len(irem) > 50:
            lines.append(f"- ... and {len(irem) - 50} more")

    joined, left = sections["ros"]
    lines.append(f"\n## RO movements  (+{len(joined)} joined, −{len(left)} left)  ← job-change triggers")
    if joined:
        lines.append("\n### Newly appointed ROs  ← highest-converting trigger per BD memo\n")
        for r in sorted(joined, key=lambda x: (x["corp_name"], x["ro_full_name"])):
            lines.append(f"- **{r['ro_full_name']}**  (`{r['ro_ceref']}`) → joined **{r['corp_name']}**  (`{r['corp_ceref']}`)")
    if left:
        lines.append("\n### ROs that departed\n")
        for r in sorted(left, key=lambda x: (x["corp_name"], x["ro_full_name"])):
            lines.append(f"- {r['ro_full_name']}  (`{r['ro_ceref']}`) ← was at {r['corp_name']}  (`{r['corp_ceref']}`)")

    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ratype", default="9")
    args = ap.parse_args()

    sections = {}
    import datetime as _dt
    new_date = _dt.date.today().isoformat()
    # Approximate "prev" date — we don't know the exact prior scrape date
    # without timestamping; the file mtime is the best proxy
    prev_p_for_date = SNAP_DIR / f"sfc_t{args.ratype}_corps_prev.csv"
    old_date = _dt.date.fromtimestamp(prev_p_for_date.stat().st_mtime).isoformat() if prev_p_for_date.exists() else "prev"

    for prefix in ("corps", "individuals", "corp_ros"):
        new_p, old_p = latest_two(prefix, args.ratype)
        new_rows, old_rows = read_csv(new_p), read_csv(old_p)
        if prefix == "corps":
            sections["corps"] = diff_by_key(new_rows, old_rows, "ceref")
        elif prefix == "individuals":
            sections["individuals"] = diff_by_key(new_rows, old_rows, "ceref")
        else:
            sections["ros"] = diff_ros(new_rows, old_rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"sfc_t{args.ratype}_diff_{new_date}_vs_{old_date}.md"
    out.write_text(fmt_md(new_date, old_date, sections), encoding="utf-8")
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
