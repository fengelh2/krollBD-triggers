# Enrichment cascade — website, scrape depth, emails

This workflow documents what we do to *enrich* an SFC register entry with the
information needed for outreach (website, on-site emails, named-RO emails). It
is the layer between the raw weekly SFC pull and a publishable trigger card.

The cascade is **lazy / on-demand**: we don't pre-enrich every entry. We enrich
a firm at the moment we need to act on it — i.e. when a C1/R1 trigger fires for
that firm and the publisher is about to draft an outreach email.

## TL;DR

| Layer | When | What |
|---|---|---|
| **Weekly batch** (auto) | Every Mon, ~30 min | `scrape_sfc → classify → verify → derive`. Basic homepage scrape + regex emails. Nothing else. |
| **On-trigger** (auto) | When a C1/R1 fires | Deep-scrape + Hunter.io for THAT firm only, gated on illiq + quota |
| **Backfill** (manual) | When Felix wants a sweep | `deep_scrape_contact_pages.py --scope X --dry-run` then re-run without `--dry-run` |

Hunter.io's 50/mo budget is reserved for trigger-time RO lookups, never burned
on speculative weekly enrichment.

---

## Cost-tiered cascades

### Website discovery (find the firm's URL)

| # | Stage | Cost | Hit-rate floor |
|---|---|---|---|
| 1 | `website_overrides.csv` | free, instant | 100% when row exists |
| 2 | SFC `/addresses` tab | free | ~52% of corps |
| 3 | SerpAPI HK-biased query | ~5k/mo free, ~3.5k burned/day | ~80% of remainder |
| 4 | Mark `not_found` | — | — |

Owned by `classify_strategy.py`. The override **must short-circuit** every
re-run — it is not advisory. If we ever drop that gate, a redesigned firm site
will silently overwrite manual fixes.

### Email discovery (find an email at the firm or for a named RO)

| # | Stage | Cost | Where |
|---|---|---|---|
| 1 | Homepage regex (during classify) | free | `classify_strategy.py:extract_emails_from_md` |
| 2 | Deep-scrape `/contact /team /people /leadership` | Firecrawl per page | `deep_scrape_contact_pages.py` |
| 3 | SerpAPI aggregator pattern | free quota | `find_email_via_search.py` |
| 4 | Hunter.io `/email-finder` for named RO | **50/mo paid cap** | `hunter_io.py` (new) |
| 5 | LinkedIn manual lookup | Felix's time | dashboard "Mark RO email manually" |

Today's measured yield on stage 2 (deep-scrape):
- 22% hit rate (20 of 89 verified-high BD firms)
- Mostly generic (`info@`, `webmaster@`) — useful as confirmed-vs-guessed but
  not directly targetable
- A handful of named-person upsides (Hamilton Lane `kmcgann@…`, Mindworks
  `operations@…`)

Stage 4 (Hunter) is **per-RO** and only worthwhile at trigger time, where we
have a specific person to find and outreach is imminent.

---

## Layer 1 — Weekly Monday batch (auto, ~30 min)

In CI this runs from `.github/workflows/weekly.yml`. For a local manual run:

```bash
cd <repo-root>          # the krollBD repo clone
python tools/scrape_sfc_register.py            # ~15 min
python tools/classify_strategy.py              # ~5 min (incremental on new/changed)
python tools/verify_emails_against_disambig.py # ~5 sec
python tools/derive_website_accuracy.py        # ~1 sec
python tools/publish_triggers_to_github.py     # ~1-3 min — triggers on-trigger cascade
```

This run does:
1. Snapshot SFC register (rotates latest→prev)
2. For each NEW or CHANGED corp, run website-discovery cascade + homepage
   scrape + regex emails. ~5-10 new firms/wk; Firecrawl spend = bounded.
3. Verify emails against firm-name (drop wrong-firm captures)
4. Compute `website_accuracy` derived column
5. Run `publish_triggers_to_github.py` — for each *trigger that fires*, run the
   on-trigger cascade (Layer 2) for that one firm + RO

What this layer does NOT do:
- No deep-scrape on register entries that fired no trigger
- No Hunter.io calls on RO appearances that fired no trigger

## Layer 2 — On-trigger cascade (auto, per-trigger)

Runs inside `publish_triggers_to_github.py` when building each trigger's
email_candidates(). For one firm/RO at a time:

```
if trigger.firm.illiquid_book_likelihood in (high, medium):
  # 1. Deep-scrape this firm if no email + verified site
  if firm.no_email and firm.website_accuracy in (verified, probable):
    deep_scrape_contact_pages(firm.ceref)
    # writes to strategy_classification.csv with idempotency stamp

  # 2. Hunter.io if trigger names an RO + named-RO email not known
  if trigger.has_named_ro and quota_check():
    hunter_io.find(domain=firm.domain, first=ro.first, last=ro.last)
    # writes to ro_enrichment.csv (new file, RO-keyed)

# 3. Fall through to pattern guesses + LinkedIn manual
```

Gates:
- `illiq != high|medium` → skip entire on-trigger cascade. Mega-bank
  subsidiaries with `skip_enrichment=1` override also short-circuit here.
- `HUNTER_REMAINING < 5` → skip Hunter, log to weekly summary, fall through.
- Already-attempted stamps (see idempotency) → skip silently.

Budget: 2-3 triggers/wk × 1 deep-scrape + 1 Hunter = ~12 Firecrawl + 12 Hunter
per month. Hunter cap is 50/mo, leaves headroom for retries.

## Layer 3 — Backfill (manual, opt-in)

When Felix wants to sweep a cohort (e.g. all unverified-BD firms):

```bash
# Dry-run first to eyeball candidates
python tools/deep_scrape_contact_pages.py --scope unverified-bd --dry-run

# Then real run if list looks right
python tools/deep_scrape_contact_pages.py --scope unverified-bd

# Or trigger from GitHub UI via the ad-hoc.yml workflow:
gh workflow run ad-hoc.yml --repo fengelh2/krollBD -f task=deep_scrape -f scope=unverified-bd
```

Available scopes: `verified | verified-high | verified-bd | probable | probable-high | probable-bd | unverified-bd`

Hunter is never auto-batched; backfills there require `--cerefs` explicit list.

---

## Idempotency stamps (on `strategy_classification.csv`)

| Column | Set by | Cleared by |
|---|---|---|
| `deep_scrape_attempted_utc` | `deep_scrape_contact_pages.py` | `website_resolved_utc` newer |
| `deep_scrape_status` | same | same |
| `hunter_attempted_utc` | `publish_triggers_to_github.py` (per-RO, in `ro_enrichment.csv`) | rarely — only on `--force` |
| `website_resolved_utc` | `classify_strategy.py` whenever `website_url` changes | — |

Rule: every stage **must** check its own stamp before running. Re-runs are free.

## Failure modes + retry policy

| Failure | Stage stamp | Retry after |
|---|---|---|
| Firecrawl rate-limit (429) | `status=rate_limited` | next Monday |
| 0 emails found after deep-scrape | `status=no_emails` | 90 days, or website_resolved_utc bumps |
| Hunter quota exhausted (402) | `status=quota_exhausted` | start of next month (quota reset) |
| Mega-bank subsidiary | `website_overrides.skip_enrichment=1` | never — manual override |

## Never-downgrade-confidence rule

When the cascade writes emails back into `strategy_classification.csv`, it must
**only add** to `emails_on_site` / `generic_emails_on_site`; never replace an
existing verified email with a lower-confidence find. A redesigned site can put
a generic `info@` over a previously captured named address otherwise.

Implemented as `merge_emails(existing, new)` in `deep_scrape_contact_pages.py`
and the equivalent in the publisher's Hunter step.

## Hunter.io quota budgeting

50 lookups/mo free. With 2-3 trigger-fired RO lookups per week (10-12/mo), this
leaves headroom for ~3x retries and the occasional Felix-initiated lookup. If
the cap ever bites, the publisher logs the skip into the dashboard's weekly
summary so Felix can see how many ROs missed enrichment that month and decide
if upgrading is worth it ($49/mo for 500 lookups).

## Known dead-ends

- **Subsidiaries of global banks** (BlackRock, Goldman, MS, HSBC, JPM, etc.) —
  SerpAPI returns parent group homepage; the classifier rejects as
  not-disambiguated. These should carry `skip_enrichment=1` in
  `website_overrides.csv` so the cascade short-circuits. They're MD-led
  account-relationships anyway, not Felix-as-IC outreach targets.
- **Family offices with no public site** — frequent. Mark `not_found` and
  flag for hand-research only.
- **Contact pages that are JS form widgets** — Firecrawl scrapes the markdown
  scaffolding but no email is exposed. Stamp `no_emails`; LinkedIn fallback.

## Cost ledger (rough, per typical week)

| Stage | Calls/week | Cost/week |
|---|---|---|
| SerpAPI | ~5-10 | ~0.2% of free quota |
| Firecrawl | ~10-20 | a few cents |
| Hunter.io | ~2-3 | ~5% of monthly free cap |
| Anthropic / DeepSeek | ~5-10 LLM classifies | ~$0.10 |

Annual budget: **negligible** as long as the cascade stays on-trigger. The
moment we re-enable speculative weekly enrichment of every register entry, we
blow Hunter's monthly cap in week 3 and start eating Firecrawl on firms that
will never produce outreach.

## When to revisit this design

- If Felix's trigger volume goes above 10/wk → need to revisit quota and may
  need paid Hunter tier.
- If 22% deep-scrape hit-rate drops below 10% → tool isn't paying for itself
  even at lazy scale; cut deep-scrape from the on-trigger cascade.
- If `not_found` stays above 1,500 firms → consider a Chinese-language search
  fallback (many HK subsidiaries' sites are zh-Hant-only).
