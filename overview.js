// overview.js — Overview view as BD command center.
// Narrative order, top to bottom:
//   1. This-week action panel  — what needs my attention NOW
//   2. BD bullseye funnel      — 720 → 637 → 201 → 67, drop-offs are unlocks
//   3. Improvement queue       — concrete next moves to widen the funnel
//   4. Database strength       — context: 1 chart + mini coverage funnel
//   5. Recent triggers         — top 10 open, age-sorted, SLA-colored

(function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));

  const SLA_BUSINESS_DAYS = 5;

  function businessDaysBetween(d1, d2) {
    if (!(d1 instanceof Date) || !(d2 instanceof Date)) return 0;
    let n = 0;
    const step = d1 < d2 ? 1 : -1;
    const cur = new Date(d1);
    while (cur.getTime() !== d2.getTime() && Math.abs(n) < 365) {
      cur.setDate(cur.getDate() + step);
      const day = cur.getDay();
      if (day !== 0 && day !== 6) n += step;
    }
    return Math.abs(n);
  }

  // ---- subsets used across panels (computed once) ----
  function buildSets(data) {
    const C = data.classification.rows;
    const corpsAll = data.corps.rows;
    const corps = corpsAll.filter(r => (r.has_active_licence || "").trim() === "Y");
    const corpsRetired = corpsAll.length - corps.length;

    const bd = C.filter(K.isBdRelevant);
    const bdSite = bd.filter(r => ["verified","probable"].includes(K.waBucket(r)));
    const bdEmail = bdSite.filter(K.hasEmail);
    const bdAum = bdEmail.filter(K.hasAum);

    const allSite = C.filter(r => ["verified","probable"].includes(K.waBucket(r)));
    const allEmail = allSite.filter(K.hasEmail);
    const allAum = allEmail.filter(K.hasAum);

    return {
      C, corps, corpsRetired,
      bd, bdSite, bdEmail, bdAum,
      allSite, allEmail, allAum,
    };
  }

  // =====================================================================
  // 1. THIS-WEEK ACTION PANEL
  // =====================================================================
  function renderActionPanel(sets) {
    const issues = (window.K.Triggers && window.K.Triggers.getOpenIssues()) || [];
    const now = new Date();
    let overdue = 0;
    for (const i of issues) {
      const created = i.created_at ? new Date(i.created_at) : null;
      if (created && businessDaysBetween(created, now) > SLA_BUSINESS_DAYS) overdue++;
    }
    const newThisWeek = issues.filter(i => {
      const c = i.created_at ? new Date(i.created_at) : null;
      return c && (now - c) < 7 * 24 * 3600 * 1000;
    }).length;
    const readyNow = sets.bdEmail.length;

    const chips = [
      {
        n: issues.length,
        l: "Open triggers",
        sub: `${newThisWeek} new this week`,
        href: "#/triggers",
        cls: "chip-action",
      },
      {
        n: overdue,
        l: `SLA-overdue (>${SLA_BUSINESS_DAYS} business days)`,
        sub: overdue ? "act before the window closes" : "all within SLA",
        href: "#/triggers",
        cls: overdue ? "chip-warn" : "chip-good",
      },
      {
        n: readyNow,
        l: "BD-relevant · ready to email",
        sub: "verified/probable site · email captured",
        href: "#/corps?illiq=bd&wa=site&email=yes",
        cls: "chip-good",
      },
    ];

    $("#ov-action").innerHTML = chips.map(c => `
      <a class="action-chip ${c.cls}" href="${c.href}">
        <span class="num">${c.n.toLocaleString()}</span>
        <span class="lbl">${c.l}</span>
        <span class="sub">${c.sub}</span>
      </a>
    `).join("");
  }

  // =====================================================================
  // 2. BD BULLSEYE FUNNEL
  // =====================================================================
  function renderBdFunnel(sets) {
    const stages = [
      {
        n: sets.bd.length,
        l: "BD-relevant",
        d: "book type = illiquids or mixed",
        href: "#/corps?illiq=bd",
      },
      {
        n: sets.bdSite.length,
        l: "+ website found",
        d: "verified or probable",
        href: "#/corps?illiq=bd&wa=site",
      },
      {
        n: sets.bdEmail.length,
        l: "+ email captured",
        d: "any verified email on site",
        href: "#/corps?illiq=bd&wa=site&email=yes",
      },
      {
        n: sets.bdAum.length,
        l: "+ AUM disclosed",
        d: "any AUM extracted from website",
        href: "#/corps?illiq=bd&wa=site&email=yes&aum=yes",
      },
    ];
    const base = stages[0].n || 1;
    const html = stages.map((s, i) => {
      const pct = Math.round(100 * s.n / base);
      const dropFromPrev = i > 0 ? (stages[i-1].n - s.n) : 0;
      const dropFromBd = base - s.n;
      const widthPct = Math.max(20, 100 * s.n / base);
      const dropLine = i > 0
        ? `<div class="funnel-drop">↓ <strong>${dropFromPrev.toLocaleString()}</strong> firms dropped here · <strong>${dropFromBd.toLocaleString()}</strong> total still missing this</div>`
        : "";
      return `
        ${dropLine}
        <a class="funnel-stage" href="${s.href}" style="width:${widthPct}%">
          <span class="stage-n">${s.n.toLocaleString()}</span>
          <span class="stage-l">${esc(s.l)}</span>
          <span class="stage-d">${esc(s.d)} · ${pct}% of BD-relevant</span>
        </a>
      `;
    }).join("");
    $("#ov-funnel").innerHTML = html;

    // call-out: biggest drop-off
    const drops = stages.slice(1).map((s, i) => ({ idx: i+1, lost: stages[i].n - s.n, from: stages[i].l, to: s.l }));
    const worst = drops.sort((a,b) => b.lost - a.lost)[0];
    if (worst) {
      $("#ov-funnel-callout").innerHTML = `
        <strong>Biggest unlock:</strong> ${worst.lost.toLocaleString()} BD-relevant firms
        sit at <em>${esc(worst.from)}</em> but fail to reach <em>${esc(worst.to)}</em>.
        Close that gap and ${worst.lost.toLocaleString()} more become actionable.
      `;
    }
  }

  // =====================================================================
  // 3. IMPROVEMENT QUEUE — reframed as unlocks
  // =====================================================================
  function renderImproveQueue(data, sets) {
    const bd = sets.bd;

    // ---- Hunter cohort: BD + verified/probable site + no NAMED email + has website
    // Split into two sub-buckets so the card is honest about partial-coverage state.
    const hunterAll = bd.filter(r =>
      ["verified", "probable"].includes(K.waBucket(r)) && !K.hasEmail(r) && K.hasWebsite(r));
    const hunterZero = hunterAll.filter(r => !K.hasGenericEmail(r));
    const hunterGenOnly = hunterAll.filter(r => K.hasGenericEmail(r));

    const noSite = bd.filter(r => !K.hasWebsite(r) || K.waBucket(r) === "not_found");

    // ---- Thin cohort: BD + suspect/unverified site. Sub-split shows how many
    // already gained ANY email from a prior deep-scrape.
    const thin = bd.filter(r => ["suspect", "unverified"].includes(K.waBucket(r)));
    const thinWithAnyEmail = thin.filter(r => K.hasEmail(r) || K.hasGenericEmail(r));
    const thinStillBare = thin.filter(r => !K.hasEmail(r) && !K.hasGenericEmail(r));

    // ---- AUM
    const noAum = bd.filter(r => !K.hasAum(r));

    // Rank: AUM-disclosed first (so non-misleading top 5), then alpha
    const rank = (rows) => rows.slice().sort((a, b) => {
      const aA = parseFloat(a.aum_usd_m) || 0;
      const bA = parseFloat(b.aum_usd_m) || 0;
      if (bA !== aA) return bA - aA;
      return (a.name_en || "").localeCompare(b.name_en || "");
    });

    const queues = [
      {
        id: "hunter", title: "Named-email lookup — Hunter.io",
        unlock: hunterAll.length, unlockLabel: "still need a named contact",
        desc: "BD-relevant · verified/probable site · no named email captured",
        breakdown: `<strong>${hunterZero.length.toLocaleString()}</strong> have no email at all · <strong>${hunterGenOnly.length.toLocaleString()}</strong> have a generic-only inbox already (info@ / contact@ / etc.)`,
        action: "Run Hunter.io /email-finder for the primary RO (50/mo free; cache-aware)",
        impact: "high", rows: rank(hunterAll),
        href: "#/corps?illiq=bd&wa=site&email=no",
      },
      {
        id: "no-site", title: "Website discovery — SerpAPI rerun",
        unlock: noSite.length, unlockLabel: "would gain a website",
        desc: "BD-relevant · no website found at all",
        action: "Add `website_overrides.csv` row or rerun classifier",
        impact: "high", rows: rank(noSite),
        href: "#/corps?illiq=bd&wa=not_found",
      },
      {
        id: "thin", title: "Suspect/unverified site — re-verify",
        unlock: thin.length, unlockLabel: "have a shaky website match",
        desc: "BD-relevant · website_accuracy flagged suspect or unverified",
        breakdown: `<strong>${thinWithAnyEmail.length.toLocaleString()}</strong> already have at least one email captured (from prior deep-scrape) · <strong>${thinStillBare.length.toLocaleString()}</strong> still have nothing — these are the highest-leverage to re-scrape`,
        action: "Deep-scrape /contact + /team, then re-classify with the new content",
        impact: "medium", rows: rank(thin),
        href: "#/corps?illiq=bd&wa=suspect",
      },
      {
        id: "no-aum", title: "AUM lookup — Form ADV / press",
        unlock: noAum.length, unlockLabel: "would gain a sizing signal",
        desc: "BD-relevant · no AUM extracted",
        action: "Hand-check Form ADV / press releases / 13F filings",
        impact: "low", rows: rank(noAum),
        href: "#/corps?illiq=bd&aum=no",
      },
    ];

    $("#ov-improve").innerHTML = queues.map(q => {
      const aumDisclosed = q.rows.slice(0, 5).filter(r => r.aum_usd_m).length;
      const sumLabel = aumDisclosed === 0
        ? "Top 5 (alphabetical — none have AUM disclosed)"
        : aumDisclosed === 5
          ? "Top 5 (by AUM)"
          : "Top 5 (AUM-disclosed first · ties alphabetical)";
      return `
      <div class="improve-card impact-${q.impact}">
        <div class="improve-head">
          <h3><a href="${q.href}">${esc(q.title)}</a></h3>
          <span class="improve-count">+${q.unlock.toLocaleString()}</span>
        </div>
        <p class="improve-desc">${esc(q.desc)}</p>
        <p class="improve-unlock"><strong>${q.unlock.toLocaleString()}</strong> firms ${esc(q.unlockLabel)} · <a href="${q.href}">see all</a></p>
        <p class="improve-action"><span class="lbl">Action:</span> ${esc(q.action)}</p>
        <details class="improve-details">
          <summary>${esc(sumLabel)}</summary>
          <ol class="improve-list">
            ${q.rows.slice(0, 5).map(r => `
              <li>
                <a href="#/corps?focus=${esc(r.ceref)}">${esc(r.name_en)}</a>
                <span class="meta">CE ${esc(r.ceref)}${r.aum_usd_m ? ` · $${esc(r.aum_usd_m)}m` : " · AUM n/a"}${r.parent_org ? ` · ${esc(r.parent_org)}` : ""}</span>
              </li>
            `).join("") || "<li class='muted-text'>(none)</li>"}
          </ol>
        </details>
      </div>
    `;
    }).join("");
  }

  // =====================================================================
  // 4. DATABASE STRENGTH — WA chart + mini coverage funnel
  // =====================================================================
  function renderWaChart(data) {
    const C = data.classification.rows;
    const tiers = ["high", "medium", "low", ""];
    const tierLabels = { "high": "illiquids", "medium": "mixed", "low": "liquids only", "": "unclassified" };
    const buckets = ["verified", "probable", "unverified", "suspect", "not_found", "unknown"];
    const colors = {
      verified: "#1b6e3a", probable: "#3b82f6", unverified: "#cdd1d6",
      suspect: "#f59e0b", not_found: "#9b1d23", unknown: "#7a818b",
    };
    const counts = {};
    for (const t of tiers) {
      counts[t] = {}; for (const b of buckets) counts[t][b] = 0;
    }
    for (const r of C) {
      const t = String(r.illiquid_book_likelihood || "").toLowerCase();
      const tier = tiers.includes(t) ? t : "";
      const b = K.waBucket(r);
      const bucket = buckets.includes(b) ? b : "unknown";
      counts[tier][bucket]++;
    }
    const totals = tiers.map(t => buckets.reduce((s, b) => s + counts[t][b], 0));
    const maxTotal = Math.max(1, ...totals);

    const W = 600, H = 220, PAD_L = 90, PAD_R = 60, PAD_T = 10, PAD_B = 26;
    const barH = (H - PAD_T - PAD_B) / tiers.length - 8;
    const xMax = W - PAD_L - PAD_R;
    let svg = "";
    tiers.forEach((t, i) => {
      const yTop = PAD_T + i * ((H - PAD_T - PAD_B) / tiers.length);
      svg += `<text x="${PAD_L - 6}" y="${yTop + barH/2 + 4}" font-size="11" text-anchor="end" fill="#3b424a" font-family="Inter,sans-serif">${tierLabels[t]}</text>`;
      let x = PAD_L;
      const total = totals[i];
      for (const b of buckets) {
        const v = counts[t][b];
        if (!v) continue;
        const w = xMax * (v / maxTotal);
        svg += `<rect x="${x}" y="${yTop}" width="${w}" height="${barH}" fill="${colors[b]}" opacity="0.9">
          <title>${tierLabels[t]} · ${b}: ${v} (${total ? Math.round(100*v/total) : 0}% of ${tierLabels[t]})</title></rect>`;
        if (w > 24) svg += `<text x="${x + w/2}" y="${yTop + barH/2 + 4}" font-size="10" text-anchor="middle" fill="#fff" font-family="Inter,sans-serif">${v}</text>`;
        x += w;
      }
      svg += `<text x="${x + 4}" y="${yTop + barH/2 + 4}" font-size="10" fill="#7a818b" font-family="Inter,sans-serif">n=${total}</text>`;
    });
    svg += `<line x1="${PAD_L}" y1="${H - PAD_B}" x2="${W - PAD_R}" y2="${H - PAD_B}" stroke="#e4e6ea"/>`;
    svg += `<text x="${PAD_L}" y="${H - 8}" font-size="10" fill="#7a818b" font-family="Inter,sans-serif">total = ${C.length.toLocaleString()} classified firms · bar width = count</text>`;
    $("#ov-wa-chart").innerHTML = svg;
    $("#ov-wa-legend").innerHTML = buckets.map(b =>
      `<span class="legend-item"><span class="swatch" style="background:${colors[b]}"></span>${b}</span>`
    ).join("");
  }

  function renderCoverageFunnel(sets) {
    const stages = [
      { n: sets.corps.length, l: "Active T9-licensed corps", retiredSub: `+${sets.corpsRetired.toLocaleString()} retired` },
      { n: sets.allSite.length, l: "Website found" },
      { n: sets.allEmail.length, l: "Email captured" },
      { n: sets.allAum.length, l: "AUM disclosed" },
    ];
    const base = stages[0].n || 1;
    $("#ov-coverage").innerHTML = stages.map(s => `
      <div class="cov-step">
        <div class="cov-n">${s.n.toLocaleString()}</div>
        <div class="cov-l">${esc(s.l)}</div>
        <div class="cov-pct">${Math.round(100 * s.n / base)}% of active</div>
        ${s.retiredSub ? `<div class="cov-sub">${esc(s.retiredSub)}</div>` : ""}
      </div>
    `).join("<div class='cov-arrow'>→</div>");
  }

  function renderPeopleRow(data) {
    const inds = data.individuals.rows;
    const pairs = data.pairs.rows;
    const C = data.classification.rows;
    const activeInds = inds.filter(r => (r.has_active_licence || "Y") === "Y").length;
    const uniqueRos = new Set(pairs.map(p => p.ro_ceref).filter(Boolean)).size;
    const totalAssignments = pairs.length;

    // RO-level email coverage (two granularities):
    //  - strict: RO whose primary-corp has a Hunter-verified named email
    //    captured (hunter_email column populated)
    //  - broad: RO at a firm with any named email (emails_on_site OR ir_email
    //    OR hunter_email). The firm-level signal projected onto its ROs.
    const firmHunterEmail = new Set();
    const firmAnyNamedEmail = new Set();
    for (const r of C) {
      const hunter = (r.hunter_email || "").trim();
      const onSite = (r.emails_on_site || "").trim();
      const ir = (r.ir_email || "").trim();
      if (hunter) firmHunterEmail.add(r.ceref);
      if (hunter || onSite || ir) firmAnyNamedEmail.add(r.ceref);
    }
    const rosWithHunter = new Set();
    const rosWithAnyNamed = new Set();
    for (const p of pairs) {
      if (firmHunterEmail.has(p.corp_ceref)) rosWithHunter.add(p.ro_ceref);
      if (firmAnyNamedEmail.has(p.corp_ceref)) rosWithAnyNamed.add(p.ro_ceref);
    }

    const multiAffil = totalAssignments - uniqueRos;
    $("#ov-people").innerHTML = `
      <div class="cov-row">
        <div class="cov-step">
          <div class="cov-n">${inds.length.toLocaleString()}</div>
          <div class="cov-l">Individuals on register</div>
          <div class="cov-sub">all-time</div>
        </div>
        <div class="cov-arrow">&rarr;</div>
        <div class="cov-step">
          <div class="cov-n">${activeInds.toLocaleString()}</div>
          <div class="cov-l">Currently active license-holders</div>
        </div>
        <div class="cov-arrow">&rarr;</div>
        <div class="cov-step cov-step-linked">
          <div class="cov-connector-tag">&uarr; employed by the 2,814 corps above</div>
          <div class="cov-n">${uniqueRos.toLocaleString()}</div>
          <div class="cov-l">Serve as RO on an active T9 corp</div>
          <div class="cov-sub">${totalAssignments.toLocaleString()} assignments &middot; ${multiAffil.toLocaleString()} ROs serve &gt;1 firm</div>
        </div>
        <div class="cov-arrow">&rarr;</div>
        <div class="cov-step">
          <div class="cov-n">${rosWithAnyNamed.size.toLocaleString()}</div>
          <div class="cov-l">At firm with named email</div>
          <div class="cov-sub">of which <strong>${rosWithHunter.size.toLocaleString()}</strong> Hunter-verified</div>
        </div>
      </div>
    `;
  }

  // =====================================================================
  // 5. RECENT TRIGGERS — age-sorted, SLA-colored
  // =====================================================================
  function renderTriggersStrip() {
    const issues = (window.K.Triggers && window.K.Triggers.getOpenIssues()) || [];
    if (!issues.length) {
      $("#ov-triggers-strip").innerHTML = `<p class="muted-text">No open triggers, or PAT not yet set. Set token above to load.</p>`;
      return;
    }
    const sorted = issues.slice().sort((a, b) =>
      new Date(a.created_at) - new Date(b.created_at)); // oldest first (SLA-risky)
    const now = new Date();
    const recent = sorted.slice(0, 10);
    $("#ov-triggers-strip").innerHTML = recent.map(i => {
      const m = window.K.Triggers && window.K.Triggers.parseMeta ? window.K.Triggers.parseMeta(i) : null;
      const created = i.created_at ? new Date(i.created_at) : null;
      const bdays = created ? businessDaysBetween(created, now) : 0;
      const slaCls = bdays > SLA_BUSINESS_DAYS ? "sla-overdue" : bdays > 3 ? "sla-warn" : "sla-ok";
      const firm = m ? m.firm : (i.title || "").replace(/^\[[^\]]+\]\s*/, "");
      const tType = m ? m.type : (((i.title || "").match(/^\[([A-Z0-9]+)\]/) || [])[1] || "");
      return `
        <a class="trigger-chip ${slaCls}" href="#/triggers">
          <span class="t-type">${esc(tType)}</span>
          <span class="t-firm">${esc(firm)}</span>
          <span class="t-meta">${bdays} bd old</span>
        </a>
      `;
    }).join("") + `<a class="trigger-chip more" href="#/triggers">+${Math.max(0, issues.length - 10)} more →</a>`;
  }

  // =====================================================================
  // RENDER
  // =====================================================================
  async function render() {
    try {
      const data = await K.loadAll();
      window.__data = data;
      const sets = buildSets(data);
      renderBdFunnel(sets);
      renderImproveQueue(data, sets);
      renderWaChart(data);
      renderCoverageFunnel(sets);
      renderPeopleRow(data);
      renderActionPanel(sets);  // last because depends on triggers
      renderTriggersStrip();
    } catch (e) {
      const target = $("#ov-action") || $("#ov-funnel");
      if (target) target.innerHTML = `<p class="loading">Failed to load: ${esc(e.message)}<br><br>Set a GitHub PAT (top right) with <code>repo</code> scope if the data lives in a private repo.</p>`;
      console.error(e);
    }
  }

  window.K = window.K || {};
  window.K.Overview = { render };
  // Refresh action panel + triggers strip when issues load
  window.addEventListener("triggers-loaded", () => {
    if (!window.__data) return;
    // Don't waste cycles re-rendering Overview when the user is on another tab.
    const overviewEl = document.getElementById("view-overview");
    if (overviewEl && overviewEl.hidden) return;
    const sets = buildSets(window.__data);
    renderActionPanel(sets);
    renderTriggersStrip();
  });
})();
