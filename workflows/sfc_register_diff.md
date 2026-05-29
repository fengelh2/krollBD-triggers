# Kroll PortVal BD — SFC register diff + classifier + dashboard

End-to-end system that detects weekly changes in the SFC Type 9 (Asset Management)
public register and turns them into actionable BD outreach.

## High-level flow

```
Every Monday:

  1. SCRAPE         scrape_sfc_register.py
                    Rotates *_latest → *_prev, then re-pulls SFC register fresh.
                    Outputs 3 snapshot CSVs (corps + individuals + corp_ros).
                    Adds website_url_sfc per corp (52% coverage) by hitting
                    each corp's /addresses tab.
                    ~15 min runtime.

  2. CLASSIFY       classify_strategy.py
                    For each new/changed corp: pick website (override > SFC > SerpAPI),
                    scrape markdown, LLM classifies strategy + extracts emails.
                    Idempotent — skips firms already classified unless website changed.
                    ~5-10 new firms/week after the one-shot baseline of 2,814.

  3. POST-PROCESS   verify_emails_against_disambig.py
                    Clears emails from wrong-firm captures (LLM uncertain + email
                    domain doesn't match firm name).
                    ~5 sec.

  3b. DERIVE        derive_website_accuracy.py
                    Adds website_accuracy column (verified/probable/unverified/
                    suspect/not_found) combining 3 LLM signals.
                    ~1 sec.

  4. DIFF + PUBLISH publish_triggers_to_github.py
                    Compares latest vs prev snapshots. For each new trigger:
                    - runs the on-trigger enrichment cascade (deep-scrape +
                      Hunter.io) for that one firm/RO — see
                      workflows/enrichment_cascade.md
                    - writes meta to data/issue_meta/{trigger_id}.json in this
                      repo (via git push, bypasses GH abuse filter)
                    - creates a minimal-body GH issue referencing the meta file
                    Dashboard shows the trigger card with strategy chips,
                    email candidates, drafted email.
                    ~1-3 min.

You: open dashboard → review cards → copy email → send → mark as reached out.
```

## File layout

Single-repo, served by GitHub Pages at https://fengelh2.github.io/krollBD/.

```
fengelh2/krollBD/                          single source of truth
├── README.md
├── requirements.txt                       pinned deps
├── .env.example                           copy to .env (gitignored) for local
├── .github/workflows/
│   ├── weekly.yml                         Mon 00:00 UTC cron
│   └── ad-hoc.yml                         manual backfills
├── data/
│   ├── snapshots/                         WEEKLY refresh
│   │   ├── sfc_t9_corps_latest.csv        ~4,290 corps · 9 cols incl. website_url_sfc
│   │   ├── sfc_t9_corps_prev.csv
│   │   ├── sfc_t9_individuals_latest.csv  ~50,768 people · parsed name fields
│   │   ├── sfc_t9_individuals_prev.csv
│   │   ├── sfc_t9_corp_ros_latest.csv     ~8,693 (corp,RO) pairs
│   │   └── sfc_t9_corp_ros_prev.csv
│   ├── strategy_classification.csv        ACCUMULATING — one row per firm
│   ├── issue_meta/                        one JSON per fired trigger
│   ├── hunter_io_cache.json               TTL-aware per-RO lookup cache
│   ├── firm_pages/{ceref}.md              scraped markdown cache (gitignored)
│   ├── name_natural_overrides.csv         manual: clean up firm names
│   └── website_overrides.csv              manual: correct wrong-website + skip_enrichment
│
├── tools/                                 the pipeline
│   ├── scrape_sfc_register.py             #1
│   ├── classify_strategy.py               #2 (sticky-fields merge on dedup)
│   ├── verify_emails_against_disambig.py  #3
│   ├── derive_website_accuracy.py         #3b
│   ├── publish_triggers_to_github.py      #4 (calls deep-scrape + Hunter inline)
│   ├── deep_scrape_contact_pages.py       cascade Layer 2 (merge-preserving writes)
│   ├── hunter_io.py                       cascade Layer 4 (quota-guarded, typed status)
│   ├── find_email_via_search.py           SerpAPI aggregator helper
│   ├── llm_router.py                      LLM provider router
│   └── diff_sfc_snapshots.py              diff helper (markdown report)
│
├── workflows/
│   ├── sfc_register_diff.md               THIS FILE — master flow
│   ├── enrichment_cascade.md              cascade design, gates, cost ledger
│   └── overrides_howto.md                 manual overrides + skip_enrichment flag
│
├── outreach_log.csv                       appended via GH Action on "Mark as reached out"
└── index.html + *.js + style.css          dashboard (served from / by GH Pages)
```

## Snapshot rotation

The "latest / prev" pattern is intentionally minimal:
- `*_latest.csv` is what's current
- `*_prev.csv` is last week, kept only for diffing
- Older snapshots are deleted on each rotation (the SFC scrape is fast + repeatable)

If you want long-term history (RO career tracking, etc.), the next architecture
step is SCD2 in Supabase — design doc in chat history but not yet built.

## Per-trigger flow (when an issue fires)

For each new trigger (C1=new corp, C2=retirement, R1=new RO, C5=rebrand):

1. publisher reads corp + RO data from snapshot CSVs
2. joins on strategy_classification.csv for firm-level meta
3. constructs email candidates:
   - verified emails from `emails_on_site` / `generic_emails_on_site` (highest confidence)
   - aggregator-declared pattern via SerpAPI → apply to RO's name
   - pattern guesses (first.last, firstlast, flast, etc.) against verified domain
   - generic inbox guesses (info@, compliance@, ir@)
4. on-trigger enrichment cascade: deep-scrape + Hunter.io fire automatically
   for illiq=high|medium firms (see workflows/enrichment_cascade.md Layer 2);
   Hunter hits land as `hunter_verified` candidates at the top of the list
5. writes meta file to `data/issue_meta/{trigger_id}.json` in this repo
6. creates GH issue with minimal body + META_FILE reference
7. dashboard reads issue, fetches meta file, renders card with confidence-colored chips

## Trigger types (currently active)

| ID | Detection | Issued? |
|---|---|---|
| C1 | New corp (in latest, not in prev) | ✅ |
| C2 | License retired (Y→N) | ✅ |
| C5 | Firm name change | ✅ |
| R1 | New RO appointed | ✅ |
| C3 | License reactivated (N→Y) | detected, not issued |
| R2 | RO departed | detected, not issued (flagged inside C2 issues) |
| I1/I2 | Individual licensed/unlicensed | detected, not issued |

## Dashboard

https://fengelh2.github.io/krollBD/

PAT-authenticated (private repo). Pulls open issues, fetches each meta file
from the repo via Contents API.

Card sections per trigger:
- Title + trigger type tag
- CE ref + SFC register link + GitHub issue link
- **Strategy chips**: website accuracy (🟢 verified / 🟦 probable / ⚪ unverified / 🟧 suspect / 🔴 not_found), illiq tier, asset_classes, AUM, parent_org, classification source
- ROs current + departed (for C2)
- Email candidates ordered by confidence (verified > high > medium > low) with copy + mailto buttons
- "Mark as reached out" button (logs to outreach_log.csv via GitHub Action)

## Override mechanisms (manual fixes)

| File | Use case |
|---|---|
| `data/website_overrides.csv` | When SerpAPI picked wrong website → see `overrides_howto.md` |
| `data/name_natural_overrides.csv` | When auto-cleaning produces a wrong-natural name (e.g. abrdn HK Ltd → "abrdn" not "abrdn Hong Kong") |

## Current bulk-classifier state (2026-05-28)

- 2,814 active Type 9 firms classified
- 1,335 (47%) with strong classification (illiq + asset_classes known)
- 714 BD-relevant (illiq=high+medium) — bullseye list
- 161 with AUM extracted
- 535 with verified emails captured (after wrong-firm cleanup)

## Honest gaps still open

- **Hunter.io not wired into publisher** — key in .env, helper module needs to call /email-finder for each RO at trigger time. ~30 min build.
- **R2/C3/I1/I2 issue creation** — diff detects, publisher doesn't auto-issue.
- **No long-term history** — only latest vs prev. SCD2 migration to Supabase is the next architecture step if needed.
- **Bulk re-scrape for thin-content firms (~291)** — partially done for BD-relevant subset (142 firms with JS-wait + /about fallback).
- **Demo-mode issue creation blocked by GitHub abuse filter today** — too many test issues this session. Real Monday volume won't trigger it.
