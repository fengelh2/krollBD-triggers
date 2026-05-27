// krollBD-triggers dashboard
// Reads open + closed issues from GitHub API, renders trigger cards and a
// reached-out-per-week chart. No auth: closing an issue happens on github.com.

const REPO = "fengelh2/krollBD-triggers";
const API = `https://api.github.com/repos/${REPO}/issues`;
const SEARCH = "https://api.github.com/search/issues";
const HIDDEN_KEY = "krollbd_hidden_ids";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let CURRENT_FILTER = "all";
let ALL_OPEN = [];
let ALL_CLOSED = [];

function hiddenIds() {
  try { return new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) || "[]")); }
  catch { return new Set(); }
}
function saveHidden(set) {
  localStorage.setItem(HIDDEN_KEY, JSON.stringify(Array.from(set)));
}

function parseMeta(body) {
  const m = body && body.match(/<!--\s*DASH_META:\s*(\{[\s\S]*?\})\s*-->/);
  if (!m) return null;
  try { return JSON.parse(m[1]); } catch { return null; }
}

async function fetchIssues(state) {
  const out = [];
  for (let page = 1; page <= 10; page++) {
    const r = await fetch(`${API}?state=${state}&per_page=100&page=${page}`,
      { headers: { "Accept": "application/vnd.github+json" } });
    if (!r.ok) throw new Error("GitHub API: " + r.status);
    const batch = await r.json();
    out.push(...batch.filter(i => !i.pull_request));
    if (batch.length < 100) break;
  }
  return out;
}

function copyToClipboard(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = "Copied";
    btn.classList.add("copied");
    setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 1500);
  });
}

function typeFromLabels(labels) {
  for (const l of labels || []) {
    if (l.name === "C1-new-corp") return "C1";
    if (l.name === "C2-retirement") return "C2";
  }
  return null;
}

// ----- ISO week helpers -----
function isoWeekKey(d) {
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  const week = Math.ceil((((t - yearStart) / 86400000) + 1) / 7);
  return `${t.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}
function weeksBack(n) {
  const out = [];
  const now = new Date();
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(now);
    d.setUTCDate(d.getUTCDate() - i * 7);
    out.push(isoWeekKey(d));
  }
  return out;
}

// ----- chart -----
function drawChart(closedByWeek) {
  const weeks = weeksBack(12);
  const c1 = weeks.map(w => closedByWeek[w]?.C1 || 0);
  const c2 = weeks.map(w => closedByWeek[w]?.C2 || 0);
  const max = Math.max(1, ...c1, ...c2, ...weeks.map((_, i) => c1[i] + c2[i]));

  const W = 600, H = 100, PAD = 6;
  const xStep = (W - PAD * 2) / Math.max(1, weeks.length - 1);
  const yScale = (v) => H - PAD - ((H - PAD * 2) * v / max);

  const polyline = (arr) =>
    arr.map((v, i) => `${PAD + i * xStep},${yScale(v)}`).join(" ");

  const svg = $("#chart");
  svg.innerHTML = `
    <line x1="0" y1="${H - PAD}" x2="${W}" y2="${H - PAD}" stroke="#e4e6ea" stroke-width="1"/>
    <polyline fill="none" stroke="#1a3554" stroke-width="1.6" points="${polyline(c1)}"/>
    <polyline fill="none" stroke="#9b1d23" stroke-width="1.6" points="${polyline(c2)}"/>
    ${c1.map((v, i) => v ? `<circle cx="${PAD + i * xStep}" cy="${yScale(v)}" r="2.5" fill="#1a3554"/>` : "").join("")}
    ${c2.map((v, i) => v ? `<circle cx="${PAD + i * xStep}" cy="${yScale(v)}" r="2.5" fill="#9b1d23"/>` : "").join("")}
    <text x="${PAD}" y="${H - 1}" font-size="8" fill="#7a818b" font-family="Inter,sans-serif">${weeks[0]}</text>
    <text x="${W - PAD - 38}" y="${H - 1}" font-size="8" fill="#7a818b" font-family="Inter,sans-serif">${weeks[weeks.length - 1]}</text>
    <text x="${PAD}" y="10" font-size="8" fill="#7a818b" font-family="Inter,sans-serif">max ${max}</text>
  `;
}

function renderCard(issue) {
  const meta = parseMeta(issue.body);
  if (!meta) return null;

  const card = document.createElement("article");
  card.className = "card";
  card.dataset.type = meta.type;
  card.dataset.id = issue.number;

  const departed = meta.ros_departed || [];
  const rosList = meta.ros || meta.ros_current || [];

  card.innerHTML = `
    <div class="head">
      <h2 class="firm">${escapeHtml(meta.firm)}</h2>
      <span class="tag ${meta.type}">${meta.type} · ${escapeHtml(meta.type_label)}</span>
    </div>
    <p class="meta">CE <code>${escapeHtml(meta.ceref)}</code> · <a href="${meta.sfc_url}" target="_blank" rel="noopener">SFC register →</a> · <a href="${issue.html_url}" target="_blank" rel="noopener">GitHub issue →</a></p>
    ${meta.address ? `<p class="addr">${escapeHtml(meta.address)}</p>` : ""}

    ${rosList.length ? `
      <div class="ros">
        <span class="lbl">${meta.type === 'C2' ? 'ROs still on file' : 'Responsible Officers'}</span>
        <ul>${rosList.map(r => `<li>${escapeHtml(r.name)} <span class="name-ceref">${escapeHtml(r.ceref)}</span></li>`).join("")}</ul>
      </div>` : ""}

    ${departed.length ? `
      <div class="ros">
        <span class="lbl warm">Departed ROs · warm-lead candidates</span>
        <ul>${departed.map(r => `<li>${escapeHtml(r.name)} <a href="https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(r.name)}" target="_blank" rel="noopener" style="font-size:10px;color:var(--muted)">LinkedIn →</a></li>`).join("")}</ul>
      </div>` : ""}

    <div class="lookups">
      <a href="https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(meta.natural)}" target="_blank" rel="noopener">LinkedIn · firm</a>
      ${meta.primary_ro ? `<a href="https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(meta.primary_ro + ' ' + meta.natural)}" target="_blank" rel="noopener">LinkedIn · ${escapeHtml(meta.primary_ro)}</a>` : ""}
      <a href="https://www.google.com/search?q=${encodeURIComponent(meta.natural + ' hong kong contact email')}" target="_blank" rel="noopener">Google · contact</a>
      <a href="https://duckduckgo.com/?q=${encodeURIComponent(meta.natural + ' hong kong asset management')}" target="_blank" rel="noopener">Find website</a>
    </div>

    <div class="email-block">
      <div class="email-row">
        <span class="label">Subject</span>
        <span class="value">${escapeHtml(meta.email_subject || "")}</span>
        <button class="btn copy" data-copy="subject">Copy subject</button>
      </div>
      <div class="email-row" style="align-items:flex-start">
        <span class="label" style="padding-top:6px">Body</span>
        <div style="flex:1">
          <pre class="email-body-text">${escapeHtml(meta.email_body || "")}</pre>
        </div>
      </div>
      <div class="actions" style="margin-top:6px">
        <button class="btn copy" data-copy="body">Copy body</button>
      </div>
    </div>

    <div class="actions">
      <button class="btn" data-action="hide-only">Hide locally</button>
      <button class="btn primary" data-action="reached-out">Mark as reached out</button>
    </div>
  `;

  card.querySelector('[data-copy="subject"]').addEventListener("click", e =>
    copyToClipboard(meta.email_subject || "", e.target));
  card.querySelector('[data-copy="body"]').addEventListener("click", e =>
    copyToClipboard(meta.email_body || "", e.target));
  card.querySelector('[data-action="reached-out"]').addEventListener("click", () => {
    const h = hiddenIds(); h.add(String(issue.number)); saveHidden(h);
    window.open(issue.html_url, "_blank", "noopener");
    refresh();
  });
  card.querySelector('[data-action="hide-only"]').addEventListener("click", () => {
    const h = hiddenIds(); h.add(String(issue.number)); saveHidden(h);
    refresh();
  });

  return card;
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function refresh() {
  const hidden = hiddenIds();
  const cards = $("#cards");
  cards.innerHTML = "";

  let visible = ALL_OPEN.filter(i => {
    const isHidden = hidden.has(String(i.number));
    if (CURRENT_FILTER === "hidden") return isHidden;
    if (isHidden) return false;
    if (CURRENT_FILTER === "all") return true;
    const m = parseMeta(i.body);
    return m && m.type === CURRENT_FILTER;
  });

  if (visible.length === 0) {
    cards.innerHTML = `<p class="loading">Nothing in this view.</p>`;
  } else {
    visible.forEach(i => {
      const c = renderCard(i);
      if (c) cards.appendChild(c);
    });
  }

  // ---- summary ----
  const openVisible = ALL_OPEN.filter(i => !hidden.has(String(i.number))).length;
  const closedCount = ALL_CLOSED.length;
  const totalCycle = ALL_OPEN.length + closedCount;
  $("#stat-open").textContent = openVisible;
  $("#stat-done").textContent = closedCount;
  const rate = totalCycle ? Math.round(100 * closedCount / totalCycle) : 0;
  $("#stat-rate").textContent = rate + "%";

  // by-type closed counts
  let c1Done = 0, c2Done = 0;
  const closedByWeek = {};
  const thisWeek = isoWeekKey(new Date());
  let weekDone = 0;
  for (const i of ALL_CLOSED) {
    const t = typeFromLabels(i.labels);
    if (t === "C1") c1Done++;
    if (t === "C2") c2Done++;
    if (i.closed_at) {
      const wk = isoWeekKey(new Date(i.closed_at));
      closedByWeek[wk] = closedByWeek[wk] || { C1: 0, C2: 0 };
      if (t) closedByWeek[wk][t]++;
      if (wk === thisWeek) weekDone++;
    }
  }
  $("#stat-c1").textContent = c1Done;
  $("#stat-c2").textContent = c2Done;
  $("#stat-week").textContent = weekDone;

  drawChart(closedByWeek);
}

async function init() {
  $$("#filters button").forEach(b => b.addEventListener("click", () => {
    $$("#filters button").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    CURRENT_FILTER = b.dataset.filter;
    refresh();
  }));
  try {
    [ALL_OPEN, ALL_CLOSED] = await Promise.all([
      fetchIssues("open"),
      fetchIssues("closed"),
    ]);
    refresh();
  } catch (e) {
    $("#cards").innerHTML = `<p class="loading">Failed to load: ${escapeHtml(e.message)}</p>`;
  }
}
init();
