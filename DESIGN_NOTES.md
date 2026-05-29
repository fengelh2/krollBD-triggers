# krollBD Dashboard — Design Notes (v8 revamp)

## What changed
The previous dashboard was a single trigger-card feed. v8 turns it into a
5-tab BD command center while preserving the trigger view byte-for-byte.

Files:
- `index.html` — tabbed shell + view containers + modal
- `style.css` — extended (legacy trigger styles preserved verbatim at the bottom)
- `app.js` — router shell (~80 lines)
- `data.js` — CSV fetch + parse + derived helpers (window.K)
- `triggers.js` — legacy trigger logic, lifted from old `app.js` v7, wrapped as `K.Triggers`
- `overview.js` — Overview view (KPIs, charts, improve queue, recent-triggers strip)
- `tables.js` — Corps / Individuals / Pairs tables with drill-down modal

No build step. No dependencies. Total added LOC ~1100.

## Routing model
Hash-based, no library.
```
#/overview                            (default)
#/corps[?wa=…&illiq=…&email=…&aum=…&focus=CEREF]
#/individuals
#/pairs
#/triggers
```
`app.js` listens to `hashchange`, hides all `.view` sections except the active
one, then calls the view's `show*()` function. Each view's data load is
idempotent and cached on `window.__data` after the first call.

The "Where to improve" queue cards link to filtered Corps views via query
params (e.g. `#/corps?wa=verified` for the Hunter.io candidate cohort).
Drill links use `?focus=CEREF` which auto-opens the modal on that row.

## Data fetch pattern
`data.js::fetchCsvText(path)` uses the GitHub Contents API:
```
GET https://api.github.com/repos/{REPO}/contents/{path}
Authorization: Bearer <PAT>   ← same PAT_KEY as triggers.js
```
- Inline base64 if `< 1 MB` (corps, ros, classification on the smaller side)
- Falls back to `download_url` (short-lived signed CDN URL) if the file is
  larger — needed for `sfc_t9_individuals_latest.csv` (~50k rows, ~5 MB).
- The CSV parser is a full RFC-4180-ish state machine (handles quoted fields,
  embedded commas, escaped quotes, CRLF, leading BOM). Replaces the simpler
  line-splitter that triggers.js uses for the outreach log.

Datasets are loaded in parallel by `K.loadAll()` and cached for the page life.

PAT scope: must be `repo` (the data CSVs live in a **private** repo). The
trigger view alone could survive with `public_repo`, but the data layer now
forces full `repo`.

## "Where to improve" logic
Five queues, each ranked by AUM descending (highest-payoff first), filtered
to the BD bullseye (illiq high+medium):

| Queue                  | Filter                                                            | Impact |
| ---------------------- | ----------------------------------------------------------------- | ------ |
| Hunter.io candidates   | verified/probable site **and** no email captured                  | high   |
| Re-run SerpAPI         | no website **or** `website_accuracy = not_found`                  | high   |
| Deep-scrape candidates | `website_accuracy ∈ {suspect, unverified}`                        | medium |
| AUM lookup             | `aum_usd_m` blank **and** `aum_raw_string` blank                  | medium |
| Specific-person email  | only generic inbox observed on site (no specific person)          | medium |

Each card shows the count, the action to take, and a top-10 details panel of
firm names + CE + AUM + parent. Clicking a firm jumps to the Corps tab with
that row pre-opened in the modal.

`BD-relevant = illiquid_book_likelihood ∈ {high, medium}` — same definition
used by `classify_strategy.py` to pick the bullseye list.

## Tables
All three tables share the same skeleton:
- Sticky header, sortable columns (click), filter inputs above
- Capped at 500 rows rendered (the underlying filter+count remains accurate)
- Click row → modal with full record + linked data (ROs for a corp,
  corp affiliations for an individual)
- Corps tab adds a "Download CSV" button that exports the **filtered** set
  with the full original column list — useful for follow-up work in Excel.

No virtualization. 500 rows × ~8 cols renders <50 ms in Chrome. If Felix wants
to remove the cap on Individuals (50k rows), we'd want windowing — current
choice favors "force a sharper filter" over "blast everything to the DOM".

## What Felix needs to do — repo README note

Add the following step to whatever weekly-run docs the publisher follows so
the data CSVs are committed alongside the trigger issues:

> **Dashboard data hand-off** — at the end of each weekly classifier run,
> copy the four CSVs into the triggers repo and commit:
> ```bash
> # from Agentic Workflows/projects/krollBD
> cp data/strategy_classification.csv                   ../../path/to/krollBD-triggers/data/
> cp data/snapshots/sfc_t9_corps_latest.csv             ../../path/to/krollBD-triggers/data/snapshots/
> cp data/snapshots/sfc_t9_individuals_latest.csv       ../../path/to/krollBD-triggers/data/snapshots/
> cp data/snapshots/sfc_t9_corp_ros_latest.csv          ../../path/to/krollBD-triggers/data/snapshots/
> cd ../../path/to/krollBD-triggers
> git add data/ && git commit -m "data refresh $(date -u +%Y-%m-%dT%H:%M:%SZ)" && git push
> ```
> The dashboard reads them through the GitHub Contents API with the same PAT
> it already uses for issue/dispatch calls (now requires the `repo` scope).
>
> Why this repo and not a separate one: keeps the PAT scope to a single
> repository and avoids cross-repo OAuth pain.

`publish_triggers_to_github.py` already does a `git push` at the end of its
run — it just needs to additionally stage the four CSVs. ~3 lines.

## Things I punted on
- **No CSV column auto-detection.** Drill-down assumes the headers in
  `strategy_classification.csv`. If the schema changes (e.g. new cols), they
  appear in the modal automatically (driven off `data.classification.headers`)
  but the table view's hand-picked 8 columns stay fixed.
- **No diff view.** "What changed this week?" comparing `*_latest` vs `*_prev`
  would be a natural next add. Skipped to keep scope tight.
- **No outreach-log analytics on Overview.** Could surface "of last-month's
  contacts, what % were illiq-high?" — would help measure if the trigger
  pipeline is hitting the bullseye. Easy add later.
- **Individuals table is 500-row capped.** For a 50k dataset that means the
  search input is mandatory — there's a hint in the placeholder but no
  warning banner when truncation hits.
- **Improve-queue "top 10 by AUM" hides the long tail.** Most BD-relevant
  firms have no disclosed AUM, so they all tie at 0 and sort alphabetically.
  That's fine but the framing could mislead. Consider a tie-break on
  `evidence_strength` or `classification_source ∈ {extracted, observed}`.

## Open questions for Felix
1. **PAT scope.** Are you OK upgrading the dashboard's PAT requirement from
   `public_repo` → `repo`? Alternative is a public mirror of just the CSVs,
   which I don't recommend (firm-level enrichment isn't meant to be public).
2. **CSV commit cadence.** Weekly is fine for trends, but the improve queue
   gets stale fast as you action items. Worth a "after each Hunter.io batch,
   re-run the classifier + push CSVs" mini-loop? Or accept weekly?
3. **Individuals view.** Is the name-search-only access pattern enough, or
   should we add a "show all ROs at illiq-high firms" cross-filter? That
   would join classification × pairs × individuals in-browser — doable but
   pushes the data layer harder.
4. **Trigger strip on Overview.** I'm showing the first 5 open issues in
   issue-list order. Should it instead surface the highest-AUM-firm trigger,
   or the freshest trigger?
