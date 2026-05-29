// tables.js — Corps / Individuals / Pairs drill-down tables, with modal expand.

(function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));

  // ---- Modal ----
  function openModal(html) {
    $("#modal-body").innerHTML = html;
    $("#modal-backdrop").hidden = false;
  }
  function closeModal() { $("#modal-backdrop").hidden = true; }
  function wireModal() {
    if (!$("#modal-close")) return;
    $("#modal-close").addEventListener("click", closeModal);
    $("#modal-backdrop").addEventListener("click", e => {
      if (e.target.id === "modal-backdrop") closeModal();
    });
    document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wireModal);
  else wireModal();

  // ---- Hash params ----
  function getHashParams() {
    const h = window.location.hash || "";
    const q = h.split("?")[1] || "";
    const params = {};
    for (const p of q.split("&").filter(Boolean)) {
      const [k, v] = p.split("=");
      params[decodeURIComponent(k)] = decodeURIComponent(v || "");
    }
    return params;
  }

  // ============================================================
  // CORPS
  // ============================================================
  let CORPS_STATE = {
    sort: { col: "name_en", dir: 1 },
    filters: { search: "", illiq: "", wa: "", email: "", ac: "", aum: "" },
  };

  function corpsRowMatch(r, f) {
    if (f.search) {
      const s = f.search.toLowerCase();
      if (!(r.name_en || "").toLowerCase().includes(s) &&
          !(r.parent_org || "").toLowerCase().includes(s) &&
          !(r.ceref || "").toLowerCase().includes(s)) return false;
    }
    if (f.illiq) {
      const tier = String(r.illiquid_book_likelihood || "").toLowerCase();
      if (f.illiq === "bd") { if (tier !== "high" && tier !== "medium") return false; }
      else if (tier !== f.illiq) return false;
    }
    if (f.wa) {
      const wa = K.waBucket(r);
      if (f.wa === "site") { if (wa !== "verified" && wa !== "probable") return false; }
      else if (wa !== f.wa) return false;
    }
    if (f.email === "yes" && !K.hasEmail(r)) return false;
    if (f.email === "no" && K.hasEmail(r)) return false;
    if (f.ac && !String(r.asset_classes || "").split(",").map(s => s.trim()).includes(f.ac)) return false;
    if (f.aum === "yes" && !K.hasAum(r)) return false;
    if (f.aum === "no" && K.hasAum(r)) return false;
    return true;
  }

  function renderCorpsTable() {
    const data = window.__data;
    if (!data) return;
    const C = data.classification.rows;
    const corpsByCeref = Object.fromEntries(data.corps.rows.map(r => [r.ceref, r]));
    const pairsByCorp = {};
    for (const p of data.pairs.rows) {
      (pairsByCorp[p.corp_ceref] = pairsByCorp[p.corp_ceref] || []).push(p);
    }

    const f = CORPS_STATE.filters;
    let rows = C.filter(r => corpsRowMatch(r, f));
    const { col, dir } = CORPS_STATE.sort;
    rows.sort((a, b) => {
      let va = a[col] || "", vb = b[col] || "";
      if (col === "aum_usd_m" || col === "illiq_rank") {
        const na = parseFloat(col === "illiq_rank" ? K.illiqRank(a.illiquid_book_likelihood) : a.aum_usd_m) || 0;
        const nb = parseFloat(col === "illiq_rank" ? K.illiqRank(b.illiquid_book_likelihood) : b.aum_usd_m) || 0;
        return (na - nb) * dir;
      }
      return String(va).localeCompare(String(vb)) * dir;
    });
    $("#corps-count").textContent = `${rows.length.toLocaleString()} of ${C.length.toLocaleString()}`;

    // Populate ac dropdown
    const acSel = $("#corps-ac");
    if (acSel && acSel.options.length <= 1) {
      const acs = new Set();
      for (const r of C) String(r.asset_classes || "").split(",").map(s => s.trim()).filter(Boolean).forEach(a => acs.add(a));
      [...acs].sort().forEach(a => {
        const o = document.createElement("option");
        o.value = a; o.textContent = a;
        acSel.appendChild(o);
      });
    }

    const COLS = [
      { k: "name_en", label: "Firm" },
      { k: "ceref", label: "CE" },
      { k: "illiq_rank", label: "illiq" },
      { k: "asset_classes", label: "Asset classes" },
      { k: "website_accuracy", label: "Site" },
      { k: "emails_on_site", label: "Email?" },
      { k: "aum_usd_m", label: "AUM $m" },
      { k: "parent_org", label: "Parent" },
    ];
    const head = `<thead><tr>${COLS.map(c => {
      const arrow = CORPS_STATE.sort.col === c.k ? (CORPS_STATE.sort.dir === 1 ? " ▲" : " ▼") : "";
      return `<th data-sort="${c.k}">${esc(c.label)}${arrow}</th>`;
    }).join("")}</tr></thead>`;
    const MAX = 500;
    const shown = rows.slice(0, MAX);
    const body = `<tbody>${shown.map(r => {
      const wa = K.waBucket(r);
      const hasE = K.hasEmail(r);
      return `<tr data-ceref="${esc(r.ceref)}">
        <td><a href="#" class="firm-link">${esc(r.name_en)}</a></td>
        <td><code>${esc(r.ceref)}</code></td>
        <td><span class="chip illiq-${esc(String(r.illiquid_book_likelihood||"").toLowerCase())}">${esc(r.illiquid_book_likelihood || "—")}</span></td>
        <td class="truncate" title="${esc(r.asset_classes)}">${esc(r.asset_classes || "—")}</td>
        <td><span class="chip wa wa-${esc(wa)}">${esc(wa)}</span></td>
        <td>${hasE ? "✓" : (K.hasGenericEmail(r) ? "<span class='muted-text'>generic</span>" : "—")}</td>
        <td class="num">${r.aum_usd_m ? Number(r.aum_usd_m).toLocaleString() : "—"}</td>
        <td class="truncate" title="${esc(r.parent_org)}">${esc(r.parent_org || "—")}</td>
      </tr>`;
    }).join("")}</tbody>`;
    const tbl = $("#corps-table");
    tbl.innerHTML = head + body;
    if (rows.length > MAX) {
      tbl.insertAdjacentHTML("afterend", ""); // noop, footer below
    }

    // sort handlers
    tbl.querySelectorAll("th[data-sort]").forEach(th => th.addEventListener("click", () => {
      const k = th.dataset.sort;
      if (CORPS_STATE.sort.col === k) CORPS_STATE.sort.dir *= -1;
      else { CORPS_STATE.sort.col = k; CORPS_STATE.sort.dir = 1; }
      renderCorpsTable();
    }));
    // row click → drill
    tbl.querySelectorAll("tbody tr").forEach(tr => tr.addEventListener("click", () => {
      const ceref = tr.dataset.ceref;
      drillCorp(ceref);
    }));

    // CSV download
    const csv = K.toCsv(rows, data.classification.headers);
    const blob = new Blob([csv], { type: "text/csv" });
    const a = $("#corps-csv");
    if (a._url) URL.revokeObjectURL(a._url);
    a._url = URL.createObjectURL(blob);
    a.href = a._url;
  }

  function drillCorp(ceref) {
    const data = window.__data;
    const cRow = data.classification.rows.find(r => r.ceref === ceref);
    const corpRow = data.corps.rows.find(r => r.ceref === ceref);
    const ros = data.pairs.rows.filter(p => p.corp_ceref === ceref);
    const rec = cRow || corpRow || {};
    const allHeaders = data.classification.headers;
    const rows = allHeaders.map(h => {
      const v = (cRow && cRow[h]) || "";
      if (!v) return "";
      return `<tr><th>${esc(h)}</th><td>${esc(String(v).slice(0, 500))}</td></tr>`;
    }).join("");
    const corpExtra = corpRow ? `
      <h4>SFC snapshot</h4>
      <table class="kv">
        <tr><th>Name (CN)</th><td>${esc(corpRow.name_chi)}</td></tr>
        <tr><th>Address</th><td>${esc(corpRow.address)}</td></tr>
        <tr><th>SFC website</th><td>${esc(corpRow.website_url_sfc) || "—"}</td></tr>
        <tr><th>Active licence</th><td>${esc(corpRow.has_active_licence)}</td></tr>
      </table>` : "";
    const roList = ros.length ? `
      <h4>Responsible Officers (${ros.length})</h4>
      <ul class="ro-list">${ros.map(r => `<li><strong>${esc(r.ro_full_name)}</strong> <code>${esc(r.ro_ceref)}</code> · RA types: ${esc(r.ro_ra_types)}</li>`).join("")}</ul>` : "";
    openModal(`
      <h3>${esc(rec.name_en || corpRow?.name_en || ceref)} <span class="muted-text">CE ${esc(ceref)}</span></h3>
      <div class="modal-grid">
        <div>
          <h4>Classification</h4>
          <table class="kv">${rows || "<tr><td class='muted-text'>(not yet classified)</td></tr>"}</table>
        </div>
        <div>${corpExtra}${roList}</div>
      </div>
    `);
  }

  function wireCorps() {
    const f = CORPS_STATE.filters;
    $("#corps-search").addEventListener("input", e => { f.search = e.target.value; renderCorpsTable(); });
    $("#corps-illiq").addEventListener("change", e => { f.illiq = e.target.value; renderCorpsTable(); });
    $("#corps-wa").addEventListener("change", e => { f.wa = e.target.value; renderCorpsTable(); });
    $("#corps-email").addEventListener("change", e => { f.email = e.target.value; renderCorpsTable(); });
    $("#corps-ac").addEventListener("change", e => { f.ac = e.target.value; renderCorpsTable(); });
    $("#corps-aum").addEventListener("change", e => { f.aum = e.target.value; renderCorpsTable(); });
  }

  async function showCorps() {
    if (!window.__data) await K.loadAll().then(d => window.__data = d);
    // Apply hash params from improve-queue links (?wa=verified etc.)
    const p = getHashParams();
    const f = CORPS_STATE.filters;
    if (p.wa) { f.wa = p.wa; $("#corps-wa").value = p.wa; }
    if (p.illiq) { f.illiq = p.illiq; $("#corps-illiq").value = p.illiq; }
    if (p.email) { f.email = p.email; $("#corps-email").value = p.email; }
    if (p.aum) { f.aum = p.aum; $("#corps-aum").value = p.aum; }
    renderCorpsTable();
    if (p.focus) drillCorp(p.focus);
  }

  // ============================================================
  // INDIVIDUALS
  // ============================================================
  let IND_STATE = { search: "", active: "", eo: "" };
  function renderIndTable() {
    const data = window.__data; if (!data) return;
    const I = data.individuals.rows;
    const pairsByRo = {};
    for (const p of data.pairs.rows) (pairsByRo[p.ro_ceref] = pairsByRo[p.ro_ceref] || []).push(p);

    const f = IND_STATE;
    let rows = I.filter(r => {
      if (f.search) {
        const s = f.search.toLowerCase();
        if (!(r.name_en || "").toLowerCase().includes(s) && !(r.ceref || "").toLowerCase().includes(s)) return false;
      }
      if (f.active && r.has_active_licence !== f.active) return false;
      if (f.eo === "Y" && r.is_active_eo !== "Y") return false;
      return true;
    });
    $("#ind-count").textContent = `${rows.length.toLocaleString()} of ${I.length.toLocaleString()}`;
    const MAX = 500;
    const shown = rows.slice(0, MAX);
    $("#ind-table").innerHTML = `
      <thead><tr><th>Name</th><th>CE</th><th>Licence</th><th>EO</th><th># corps</th></tr></thead>
      <tbody>${shown.map(r => `
        <tr data-ceref="${esc(r.ceref)}">
          <td><a href="#" class="ind-link">${esc(r.name_en)}</a></td>
          <td><code>${esc(r.ceref)}</code></td>
          <td>${esc(r.has_active_licence)}</td>
          <td>${esc(r.is_active_eo)}</td>
          <td>${(pairsByRo[r.ceref] || []).length}</td>
        </tr>`).join("")}</tbody>
    `;
    $("#ind-table").querySelectorAll("tbody tr").forEach(tr => tr.addEventListener("click", () => drillInd(tr.dataset.ceref)));
  }
  function drillInd(ceref) {
    const data = window.__data;
    const ind = data.individuals.rows.find(r => r.ceref === ceref) || {};
    const pairs = data.pairs.rows.filter(p => p.ro_ceref === ceref);
    openModal(`
      <h3>${esc(ind.name_en || ceref)} <span class="muted-text">CE ${esc(ceref)}</span></h3>
      <p>Chinese name: ${esc(ind.name_chi) || "—"} · Active licence: ${esc(ind.has_active_licence)} · EO: ${esc(ind.is_active_eo)}</p>
      <h4>Corp affiliations (${pairs.length})</h4>
      <ul class="ro-list">${pairs.map(p => `
        <li><a href="#/corps?focus=${esc(p.corp_ceref)}" onclick="document.getElementById('modal-close').click()"><strong>${esc(p.corp_name)}</strong></a> <code>${esc(p.corp_ceref)}</code> · RA types: ${esc(p.ro_ra_types)}</li>
      `).join("") || "<li class='muted-text'>(none)</li>"}</ul>
    `);
  }
  function wireInd() {
    $("#ind-search").addEventListener("input", e => { IND_STATE.search = e.target.value; renderIndTable(); });
    $("#ind-active").addEventListener("change", e => { IND_STATE.active = e.target.value; renderIndTable(); });
    $("#ind-eo").addEventListener("change", e => { IND_STATE.eo = e.target.value; renderIndTable(); });
  }
  async function showInd() {
    if (!window.__data) await K.loadAll().then(d => window.__data = d);
    renderIndTable();
  }

  // ============================================================
  // PAIRS
  // ============================================================
  let PAIRS_STATE = { corp: "", ro: "" };
  function renderPairsTable() {
    const data = window.__data; if (!data) return;
    const P = data.pairs.rows;
    const cQ = PAIRS_STATE.corp.toLowerCase();
    const rQ = PAIRS_STATE.ro.toLowerCase();
    let rows = P.filter(p =>
      (!cQ || (p.corp_name || "").toLowerCase().includes(cQ) || (p.corp_ceref || "").toLowerCase().includes(cQ)) &&
      (!rQ || (p.ro_full_name || "").toLowerCase().includes(rQ) || (p.ro_ceref || "").toLowerCase().includes(rQ))
    );
    $("#pairs-count").textContent = `${rows.length.toLocaleString()} of ${P.length.toLocaleString()}`;
    const MAX = 500;
    const shown = rows.slice(0, MAX);
    $("#pairs-table").innerHTML = `
      <thead><tr><th>Corp</th><th>Corp CE</th><th>RO</th><th>RO CE</th><th>RA types</th></tr></thead>
      <tbody>${shown.map(p => `
        <tr>
          <td><a href="#/corps?focus=${esc(p.corp_ceref)}">${esc(p.corp_name)}</a></td>
          <td><code>${esc(p.corp_ceref)}</code></td>
          <td>${esc(p.ro_full_name)}</td>
          <td><code>${esc(p.ro_ceref)}</code></td>
          <td>${esc(p.ro_ra_types)}</td>
        </tr>`).join("")}</tbody>
    `;
  }
  function wirePairs() {
    $("#pairs-search-corp").addEventListener("input", e => { PAIRS_STATE.corp = e.target.value; renderPairsTable(); });
    $("#pairs-search-ro").addEventListener("input", e => { PAIRS_STATE.ro = e.target.value; renderPairsTable(); });
  }
  async function showPairs() {
    if (!window.__data) await K.loadAll().then(d => window.__data = d);
    renderPairsTable();
  }

  // ---- init wiring (DOM is already parsed since scripts are at end of body) ----
  function wireAll() { wireCorps(); wireInd(); wirePairs(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wireAll);
  else wireAll();

  window.K = window.K || {};
  window.K.Tables = { showCorps, showInd, showPairs, getHashParams };
})();
