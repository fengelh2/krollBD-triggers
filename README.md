# krollBD-triggers

Public dashboard: **https://fengelh2.github.io/krollBD-triggers/**

Each issue = one BD trigger (new SFC Type 9 corp, license retirement, etc.)
Generated weekly by the scraper at `tools/krollBD/` in the
[Agentic Workflows](https://github.com/) workspace.

## Workflow
1. Weekly: run `scrape_sfc_register.py` → `publish_triggers_to_github.py`
2. Open the dashboard, copy the email subject + body, send via your client
3. Click **Mark as reached out** → hides the card locally + opens the issue → click Close
4. Summary shows total this cycle, reached-out count, completion %
