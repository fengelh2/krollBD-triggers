# krollBD

Kroll Portfolio Valuation — BD pipeline for Hong Kong SFC Type 9 (Asset Management) firms.

**Dashboard**: https://fengelh2.github.io/krollBD/

This repo is the single source of truth: code, data, dashboard, scheduled runs.

## What this does

Weekly, it pulls the SFC public register, diffs it against last week, classifies new/changed firms by their BD relevance to Kroll PortVal (illiquid book likelihood, asset classes, AUM), enriches a small set of trigger-firing firms with deep-scrape + Hunter.io for verified emails, and surfaces actionable triggers (C1 = new corp, C2 = retirement, C5 = rebrand, R1 = new RO) as GitHub Issues with drafted outreach emails.

You triage on the dashboard, copy the email, send it, click "Mark as reached out".

## Architecture (at a glance)

```
┌────────────────────────────────────────────────────────────────┐
│  GitHub Actions (weekly cron, Mon 00:00 UTC)                    │
│                                                                  │
│   tools/scrape_sfc_register.py                                  │
│   → tools/classify_strategy.py                                  │
│   → tools/verify_emails_against_disambig.py                     │
│   → tools/derive_website_accuracy.py                            │
│   → tools/publish_triggers_to_github.py                         │
│       └── for each firing trigger:                              │
│            tools/deep_scrape_contact_pages.py (in-process)      │
│            tools/hunter_io.py (in-process, quota-guarded)       │
│                                                                  │
│   → commit + push data/ updates                                 │
│   → creates GitHub Issues with meta refs                        │
└────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────────┐
│  GitHub Pages (static dashboard, served from repo root)         │
│                                                                  │
│   index.html + overview.js + tables.js + triggers.js + data.js  │
│   fetches CSVs from /data/ via GitHub Contents API              │
│   fetches issues + meta via gh issues API                       │
└────────────────────────────────────────────────────────────────┘
```

## Repo layout

```
.
├── README.md                          this file
├── requirements.txt                   pinned Python deps
├── .env.example                       copy to .env and fill in
├── .github/workflows/
│   ├── weekly.yml                     Monday cron — full pipeline
│   └── ad-hoc.yml                     manual dispatch — backfills
├── tools/                             Python pipeline
│   ├── scrape_sfc_register.py
│   ├── classify_strategy.py
│   ├── verify_emails_against_disambig.py
│   ├── derive_website_accuracy.py
│   ├── deep_scrape_contact_pages.py
│   ├── hunter_io.py
│   ├── publish_triggers_to_github.py
│   ├── find_email_via_search.py
│   ├── diff_sfc_snapshots.py
│   └── llm_router.py                  LLM provider router
├── workflows/                         markdown SOPs (the docs)
│   ├── sfc_register_diff.md           master pipeline
│   ├── enrichment_cascade.md          deep-scrape + Hunter cascade design
│   └── overrides_howto.md             how to fix a wrong website match
├── data/
│   ├── snapshots/                     weekly SFC pulls (latest+prev rotation)
│   ├── strategy_classification.csv    accumulating per-firm enrichment
│   ├── issue_meta/                    one JSON per trigger (dashboard reads these)
│   ├── website_overrides.csv          manual: correct wrong websites + skip_enrichment
│   ├── name_natural_overrides.csv     manual: clean firm names
│   ├── hunter_io_cache.json           per-RO Hunter result cache
│   └── firm_pages/                    (gitignored — scraped page cache, regen'd)
├── outreach_log.csv                   appended via GH Action when "Mark as reached out"
└── index.html, app.js, style.css,     dashboard (served by GH Pages from /)
    data.js, overview.js, tables.js,
    triggers.js, DESIGN_NOTES.md
```

## Local development

```bash
git clone https://github.com/fengelh2/krollBD.git
cd krollBD
python -m venv .venv
. .venv/bin/activate           # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env           # then edit, fill in your API keys
```

Run any tool exactly as it runs in CI:

```bash
python tools/scrape_sfc_register.py
python tools/classify_strategy.py
python tools/classify_strategy.py --cerefs ASK527,BUY347 --force   # re-classify specific firms
python tools/deep_scrape_contact_pages.py --scope verified-bd --dry-run
python tools/publish_triggers_to_github.py --dry-run
```

When you push to `main`, the dashboard auto-updates (GH Pages serves the new files in ~30s).
The next Monday's cron picks up any code changes the same way.

## GitHub Actions — production runtime

| Workflow | When | What |
|---|---|---|
| **weekly.yml** | Mon 00:00 UTC (= 08:00 HKT) automatically; also manual dispatch | Full pipeline: scrape → classify → verify → derive → publish triggers (with on-trigger enrichment cascade) → commit + push data |
| **ad-hoc.yml** | Manual dispatch only | Run a single backfill task: deep-scrape on a chosen scope, or re-classify chosen CE refs |

Trigger manually:
- GitHub UI: `Actions` tab → pick workflow → `Run workflow`
- CLI: `gh workflow run weekly.yml` / `gh workflow run ad-hoc.yml -f task=deep_scrape -f scope=verified-bd`

Both workflows share the `weekly` concurrency group so they never race on `data/`.

## Secrets

Local: `.env` (gitignored).
GitHub Actions: `Settings → Secrets and variables → Actions`.

| Key | Used for |
|---|---|
| `SERPAPI_KEY` | Website + email aggregator search (~5k/mo free) |
| `FIRECRAWL_API_KEY` | SPA-aware page scrape (paid) |
| `HUNTER_API_KEY` | Verified named-RO email finder (50/mo free) |
| `HUNTER_EMAIL` | Hunter contact metadata |
| `DEEPSEEK_API_KEY` | Cheap classifier LLM |
| `ANTHROPIC_API_KEY` | Fallback classifier LLM |
| `GITHUB_TOKEN` | `gh` CLI issue creation + repo push (auto in Actions) |

Mirror local `.env` to GH Actions in one command:
```bash
for k in SERPAPI_KEY FIRECRAWL_API_KEY HUNTER_API_KEY HUNTER_EMAIL DEEPSEEK_API_KEY ANTHROPIC_API_KEY; do
  gh secret set "$k" --repo fengelh2/krollBD --body "$(grep "^$k=" .env | cut -d= -f2-)"
done
```

(`GITHUB_TOKEN` is auto-injected by Actions, no need to set.)

## Read the workflow docs

- `workflows/sfc_register_diff.md` — the master pipeline, end to end
- `workflows/enrichment_cascade.md` — when each enrichment stage runs, gates, quota policy
- `workflows/overrides_howto.md` — how to fix a wrong website match + how to flag mega-bank subsidiaries with `skip_enrichment=1`

## Cost ceiling

All free tiers, ~$0/mo at the current weekly cadence. See `workflows/enrichment_cascade.md` for the cost ledger.
