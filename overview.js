// overview.js — Overview view: headline counts, WA chart, "where to improve" queue.

(function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));

  // ---- KPIs ----
  function renderKpis(data) {
    const C = data.classification.rows;
    const corps = data.corps.rows;
    const inds = data.individuals.rows;
    const pairs = data.pairs.rows;

    const verifiedSite = C.filter(r => K.waBucket(r) === "verified").length;
    const probableSite = C.filter(r => K.waBucket(r) === "probable").length;
    const withEmail = C.filter(K.hasEmail).length;
    const bdRelevant = C.filter(K.isBdRelevant).length;
    const withAum = C.filter(K.hasAum).length;

    const kpis = [
      { n: corps.length, l: "Active corps", t: "#/corps" },
      { n: inds.length, l: "Individuals", t: "#/individuals" },
      { n: pairs.length, l: "Corp&harr;RO pairs", t: "#/pairs" },
      { n: C.length, l: "Firms classified", t: "#/corps" },
      { n: verifiedSite + probableSite, l: "Website verified / probable", sub: `${verifiedSite} verified · ${probableSite} probable`, t: "#/corps?wa=verified" },
      { n: withEmail, l: "Firms with email captured", t: "#/corps?email=yes" },
      { n: bdRelevant, l: "BD-relevant (illiq high+med)", t: "#/corps?illiq=high" },
      { n: withAum, l: "AUM disclosed", t: "#/corps?aum=yes" },
    ];
    $("#ov-kpis").innerHTML = kpis.map(k => `
      <a class="kpi" href="${k.t}">
        <span class="num">${k.n.toLocaleString()}</span>
        <span class="lbl">${k.l}</span>
        ${k.sub ? `<span class="sub">${k.sub}</span>` : ""}
      </a>
    `).join("");
  }

  // ---- WA chart: stacked bar (one bar per illiq tier, segments per wa bucket) ----
  function renderWaChart(data) {
    const C = data.classification.rows;
    const tiers = ["high", "medium", "low", ""];
    const tierLabels = { "high": "illiq high", "medium": "illiq medium", "low": "illiq low", "": "blank" };
    const buckets = ["verified", "probable", "unverified", "suspect", "not_found", "unknown"];
    const colors = {
      verified: "#1b6e3a", probable: "#3b82f6", unverified: "#cdd1d6",
      suspect: "#f59e0b", not_found: "#9b1d23", unknown: "#7a818b",
    };

    // counts[tier][bucket]
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

    const W = 600, H = 220, PAD_L = 80, PAD_R = 16, PAD_T = 18, PAD_B = 28;
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
          <title>${tierLabels[t]} · ${b}: ${v} (${total ? Math.round(100*v/total) : 0}% of tier)</title></rect>`;
        if (w > 24) svg += `<text x="${x + w/2}" y="${yTop + barH/2 + 4}" font-size="10" text-anchor="middle" fill="#fff" font-family="Inter,sans-serif">${v}</text>`;
        x += w;
      }
      svg += `<text x="${x + 4}" y="${yTop + barH/2 + 4}" font-size="10" fill="#7a818b" font-family="Inter,sans-serif">n=${total}</text>`;
    });
    // axis
    svg += `<line x1="${PAD_L}" y1="${H - PAD_B}" x2="${W - PAD_R}" y2="${H - PAD_B}" stroke="#e4e6ea"/>`;
    svg += `<text x="${PAD_L}" y="${H - 8}" font-size="10" fill="#7a818b" font-family="Inter,sans-serif">bar width = count (max ${maxTotal})</text>`;
    $("#ov-wa-chart").innerHTML = svg;
    $("#ov-wa-legend").innerHTML = buckets.map(b =>
      `<span class="legend-item"><span class="swatch" style="background:${colors[b]}"></span>${b}</span>`
    ).join("");
  }

  // ---- Asset class chart (BD-relevant only) ----
  function renderAcChart(data) {
    const rows = data.classification.rows.filter(K.isBdRelevant);
    const counts = {};
    for (const r of rows) {
      const acs = String(r.asset_classes || "").split(",").map(s => s.trim()).filter(Boolean);
      if (!acs.length) counts["(blank)"] = (counts["(blank)"] || 0) + 1;
      for (const ac of acs) counts[ac] = (counts[ac] || 0) + 1;
    }
    const sorted = Object.entries(counts).sort((a,b) => b[1] - a[1]).slice(0, 12);
    const W = 600, H = 220, PAD_L = 130, PAD_R = 60, PAD_T = 12, PAD_B = 16;
    const xMax = W - PAD_L - PAD_R;
    const barH = (H - PAD_T - PAD_B) / Math.max(1, sorted.length) - 4;
    const max = Math.max(1, ...sorted.map(e => e[1]));
    let svg = "";
    sorted.forEach(([k, v], i) => {
      const y = PAD_T + i * ((H - PAD_T - PAD_B) / sorted.length);
      const w = xMax * (v / max);
      svg += `<text x="${PAD_L - 6}" y="${y + barH/2 + 4}" font-size="11" text-anchor="end" fill="#3b424a" font-family="Inter,sans-serif">${k}</text>`;
      svg += `<rect x="${PAD_L}" y="${y}" width="${w}" height="${barH}" fill="#1a3554" opacity="0.85"/>`;
      svg += `<text x="${PAD_L + w + 5}" y="${y + barH/2 + 4}" font-size="11" fill="#3b424a" font-family="Inter,sans-serif">${v}</text>`;
    });
    $("#ov-ac-chart").innerHTML = svg || `<text x="20" y="40" font-size="12" fill="#7a818b">No BD-relevant firms with asset_classes set.</text>`;
  }

  // ---- "Where to improve" queue ----
  // Each entry: a queue of firms ranked by BD payoff.
  function renderImproveQueue(data) {
    const C = data.classification.rows;
    const bd = C.filter(K.isBdRelevant);

    // 1. BD-relevant + verified/probable site + no email → Hunter.io candidates
    const hunter = bd.filter(r =>
      ["verified", "probable"].includes(K.waBucket(r)) &&
      !K.hasEmail(r) && K.hasWebsite(r)
    );
    // 2. BD-relevant + no website → SerpAPI re-run with override
    const noSite = bd.filter(r => !K.hasWebsite(r) || K.waBucket(r) === "not_found");
    // 3. Thin content: website_accuracy in {suspect, unverified} AND BD-relevant
    const thin = bd.filter(r => ["suspect", "unverified"].includes(K.waBucket(r)));
    // 4. BD-relevant + no AUM
    const noAum = bd.filter(r => !K.hasAum(r));
    // 5. BD-relevant + verified site + only generic email (no specific person)
    const onlyGeneric = bd.filter(r =>
      ["verified", "probable"].includes(K.waBucket(r)) &&
      !K.hasEmail(r) && K.hasGenericEmail(r)
    );

    // Bonus: rank within each queue by AUM (disclosed → high payoff first), then alpha
    const rank = (rows) => rows.slice().sort((a, b) => {
      const aA = parseFloat(a.aum_usd_m) || 0;
      const bA = parseFloat(b.aum_usd_m) || 0;
      if (bA !== aA) return bA - aA;
      return (a.name_en || "").localeCompare(b.name_en || "");
    });

    const queues = [
      { id: "hunter", title: "Hunter.io candidates", desc: "BD-relevant · verified/probable site · no email captured", action: "Run Hunter.io domain-search per CEREF", impact: "high", rows: rank(hunter) },
      { id: "no-site", title: "Re-run SerpAPI with override", desc: "BD-relevant · website not found", action: "Add website_overrides.csv row or rerun classifier", impact: "high", rows: rank(noSite) },
      { id: "thin",   title: "Deep-scrape candidates", desc: "BD-relevant · site flagged suspect/unverified", action: "Deep-scrape with JS-wait; re-classify", impact: "medium", rows: rank(thin) },
      { id: "no-aum", title: "AUM lookup candidates", desc: "BD-relevant · no AUM extracted", action: "Hand-check Form ADV / press / 13F", impact: "medium", rows: rank(noAum) },
      { id: "only-generic", title: "Specific-person email lookups", desc: "BD-relevant · only generic inbox on site", action: "LinkedIn / Hunter person-finder for primary RO", impact: "medium", rows: rank(onlyGeneric) },
    ];

    $("#ov-improve").innerHTML = queues.map(q => `
      <div class="improve-card impact-${q.impact}">
        <div class="improve-head">
          <h3>${esc(q.title)}</h3>
          <span class="improve-count">${q.rows.length}</span>
        </div>
        <p class="improve-desc">${esc(q.desc)}</p>
        <p class="improve-action"><span class="lbl">Action:</span> ${esc(q.action)}</p>
        <details class="improve-details">
          <summary>Top 10 firms (by AUM)</summary>
          <ol class="improve-list">
            ${q.rows.slice(0, 10).map(r => `
              <li>
                <a href="#/corps?focus=${esc(r.ceref)}">${esc(r.name_en)}</a>
                <span class="meta">CE ${esc(r.ceref)}${r.aum_usd_m ? ` · $${esc(r.aum_usd_m)}m` : ""}${r.parent_org ? ` · ${esc(r.parent_org)}` : ""}</span>
              </li>
            `).join("") || "<li class='muted-text'>(none)</li>"}
          </ol>
        </details>
      </div>
    `).join("");
  }

  // ---- Recent triggers strip (uses K.Triggers data once loaded) ----
  function renderTriggersStrip() {
    const issues = (window.K.Triggers && window.K.Triggers.getOpenIssues()) || [];
    if (!issues.length) {
      $("#ov-triggers-strip").innerHTML = `<p class="muted-text">No open triggers, or PAT not set.</p>`;
      return;
    }
    const recent = issues.slice(0, 5);
    $("#ov-triggers-strip").innerHTML = recent.map(i => {
      const m = window.K.Triggers.parseMeta(i);
      if (!m) return "";
      return `
        <a class="trigger-chip type-${esc(m.type)}" href="#/triggers">
          <span class="t-type">${esc(m.type)}</span>
          <span class="t-firm">${esc(m.firm)}</span>
          <span class="t-meta">${esc(m.type_label || "")}</span>
        </a>
      `;
    }).join("") + `<a class="trigger-chip more" href="#/triggers">+${Math.max(0, issues.length - 5)} more &rarr;</a>`;
  }

  async function render() {
    try {
      const data = await K.loadAll();
      window.__data = data;
      renderKpis(data);
      renderWaChart(data);
      renderAcChart(data);
      renderImproveQueue(data);
      renderTriggersStrip();
    } catch (e) {
      $("#ov-kpis").innerHTML = `<p class="loading">Failed to load: ${esc(e.message)}<br><br>Set a GitHub PAT (top right) with <code>repo</code> scope if the data lives in a private repo.</p>`;
      console.error(e);
    }
  }

  window.K = window.K || {};
  window.K.Overview = { render };
  window.addEventListener("triggers-loaded", renderTriggersStrip);
})();
