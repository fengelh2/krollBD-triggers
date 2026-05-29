# How to override a wrong website

## When you'd use this
You're reviewing a BD trigger on the dashboard and notice the **firm's website
doesn't match the firm name** — e.g.:
- "MFS International (HK)" with website `https://hkifa.org.hk/` ← obviously not MFS
- "Aisance Asset Management" with `trinitusam.com` ← different firm
- The website chip shows 🟧 **suspect** or you spot it manually

This means SerpAPI's fallback picked the wrong site. You can correct it in 30 seconds.

## The fix (one line in a CSV)

Open the file in any text editor:

```
projects/krollBD/data/website_overrides.csv
```

Add ONE row at the bottom:

```csv
ceref,corrected_url,reason,marked_at
ASK527,https://www.mfs.com/who-we-are/our-locations/asia-pacific.html,MFS HK - wrong site was hkifa.org.hk,2026-05-28
```

Columns:
- **`ceref`** — the firm's SFC CE reference (visible on the dashboard card, e.g. `ASK527`)
- **`corrected_url`** — the URL you've verified is correct (you can confirm by Googling the firm or checking LinkedIn)
- **`reason`** — short note for future you (so you remember WHY you overrode)
- **`marked_at`** — today's date

Save the file. Done.

## What happens next

| When | What |
|---|---|
| **Next weekly classifier run** (Monday morning) | Auto-picks up your override. The firm gets re-classified using your URL. |
| **Manual re-classify NOW** | Run `python projects/krollBD/tools/classify_strategy.py --cerefs ASK527 --force` |
| **Effect on the dashboard** | Next time you view the BD trigger card for that firm, the website + emails + strategy are based on the corrected URL. The chip should turn 🟢 verified or 🟦 probable. |

## Priority order the classifier uses

```
1. website_overrides.csv      ← you wrote this manually — TRUST FIRST
2. corps_latest.website_url_sfc   ← what SFC published
3. SerpAPI fallback search    ← Google-found candidate
4. (none)                     ← marked no_site_found
```

Your override always wins.

## How to find the right CE ref

On the dashboard trigger card, the **CE reference** is shown in the metadata row right under the firm name (e.g. `CE ref: ASK527`). Copy that.

You can also look it up directly on SFC:
- Search by firm name at https://apps.sfc.hk/publicregWeb/searchByName?locale=en
- The CE ref appears in the result row

## How to find the correct website

In order of how I'd try:
1. **Google the firm name** + "official site" / "Hong Kong" / "asset management"
2. **LinkedIn** — search the firm's company page; the "website" field on the profile is usually right
3. **Cross-reference with their SFC register page** — the address sometimes appears in firm marketing materials linking back to the corporate site
4. If parent firm is known (e.g. "Amundi" for "Amundi HK"), use the parent's site (e.g. amundi.com)

## Sanity-checking your override before saving

Before adding a row, open the URL in a browser. Check that:
- ✅ The page loads (not 404)
- ✅ The firm name appears somewhere (page title, footer, About page)
- ✅ It's not a redirect to a different firm

## How to remove / change an override

Just edit the CSV. Delete the row (or change the URL) and save. Re-run the classifier with `--force` on that ceref to pick up the change.

## Bulk-overriding multiple firms at once

Just add multiple rows — one per ceref:

```csv
ceref,corrected_url,reason,marked_at
ASK527,https://www.mfs.com/...,MFS HK - was hkifa.org.hk,2026-05-28
BUY347,https://www.atmasset.com,ATM Asset Mgmt - was databasesets directory,2026-05-29
AAF157,https://aisance.com.hk,Aisance - was trinitusam,2026-05-29
```

Then either let Monday's weekly run pick them all up, or force a batch re-run:

```powershell
python projects/krollBD/tools/classify_strategy.py --cerefs ASK527,BUY347,AAF157 --force
```

## Bonus column: `skip_enrichment`

Some firms are **mega-bank subsidiaries** (BlackRock HK, Goldman Asia, HSBC AM, JPM
Asset Management HK, Morgan Stanley Bank Asia, ICBC Asia, BoA HK, etc.). SerpAPI
will keep returning the global parent's homepage, and the on-trigger enrichment
cascade (deep-scrape + Hunter.io) will keep burning quota for zero usable result —
those firms don't expose HK-RO emails anywhere and outreach to them is MD-led, not
IC-led anyway.

For each such firm, add `skip_enrichment` = `1` to the override row. The publisher
will then short-circuit the on-trigger cascade for that ceref. Trigger card still
fires; it just shows "parent org: BlackRock" and skips the deep-scrape / Hunter
call.

```csv
ceref,corrected_url,reason,marked_at,skip_enrichment
AAB026,https://jpmorgan.com,JPM HK - parent-group page only,2026-05-29,1
AAB027,https://www.jpmorgan.com,JPM HK - same,2026-05-29,1
AAK796,https://bankofamerica.com,BoA HK - parent only,2026-05-29,1
```

Accepted truthy values: `1`, `true`, `yes`, `Y`. Blank/missing column = enrichment
runs normally.

## Don't add overrides for these cases

- **Right firm but thin content** (chip = 🟦 probable or ⚪ unverified) — the site IS correct, just JS-rendered or sparse. Not worth overriding.
- **No website found** (chip = 🔴 not_found) — the firm genuinely has no public site. An override only helps if you've found one we missed.

## TL;DR

```
See suspect website on dashboard
  → Open projects/krollBD/data/website_overrides.csv
  → Add one row: ceref,corrected_url,reason,date
  → Save
  → Next weekly run picks it up (or force now with --cerefs CEREF --force)
```
