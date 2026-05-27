// krollBD-triggers dashboard
// Source of triggers: open GitHub issues (read-only fetch).
// Source of "reached out" state: browser localStorage (per-browser).
// No auth, no new tabs.

const REPO = "fengelh2/krollBD-triggers";
const API = `https://api.github.com/repos/${REPO}/issues`;
const REACHED_KEY = "krollbd_reached_out_v2";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let CURRENT_FILTER = "all";
let ALL_OPEN = [];

// ---- localStorage state ----
// shape: { "<issueNumber>": { reachedAt: ISO, type: "C1"|"C2", firm: "...", title: "..." } }
function getReached() {
  try { return JSON.parse(localStorage.getItem(REACHED_KEY) || "{}"); }
  catch { return {}; }
}
function setReached(obj) { localStorage.setItem(REACHED_KEY, JSON.stringify(obj)); }
function markReached(issue, meta) {
  const r = getReached();
  r[String(issue.number)] = {
    reachedAt: new Date().toISOString(),
    type: meta.type,
    firm: meta.firm,
    title: issue.title,
  };
  setReached(r);
}
function unmarkReached(issueNumber) {
  const r = getReached();
  delete r[String(issueNumber)];
  setReached(r);
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

// ---- ISO week ----
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

// ---- chart ----
function drawChart(reached) {
  const weeks = weeksBack(12);
  const byWeek = {};
  for (const w of weeks) byWeek[w] = { C1: 0, C2: 0 };
  for (const k of Object.keys(reached)) {
    const r = reached[k];
    const wk = isoWeekKey(new Date(r.reachedAt));
    if (byWeek[wk]) byWeek[wk][r.type] = (byWeek[wk][r.type] || 0) + 1;
  }
  const c1 = weeks.map(w => byWeek[w].C1);
  const c2 = weeks.map(w => byWeek[w].C2);
  const max = Math.max(1, ...c1, ...c2);

  const W = 600, H = 100, PAD = 6;
  const xStep = (W - PAD * 2) / Math.max(1, weeks.length - 1);
  const yScale = (v) => H - PAD - ((H - PAD * 2) * v / max);
  const polyline = (arr) => arr.map((v, i) => `${PAD + i * xStep},${yScale(v)}`).join(" ");

  $("#chart").innerHTML = `
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

function renderCard(issue, opts = {}) {
  const meta = parseMeta(issue.body);
  if (!meta) return null;

  const card = document.createElement("article");
  card.className = "card";
  card.dataset.type = meta.type;
  card.dataset.id = issue.number;

  const departed = meta.ros_departed || [];
  const rosList = meta.ros || meta.ros_current || [];
  const isDone = opts.done;

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
      ${isDone
        ? `<button class="btn" data-action="undo">Undo · move back to action list</button>`
        : `<button class="btn primary" data-action="reached-out">Mark as reached out</button>`}
    </div>
  `;

  card.querySelector('[data-copy="subject"]').addEventListener("click", e =>
    copyToClipboard(meta.email_subject || "", e.target));
  card.querySelector('[data-copy="body"]').addEventListener("click", e =>
    copyToClipboard(meta.email_body || "", e.target));
  const ro = card.querySelector('[data-action="reached-out"]');
  if (ro) ro.addEventListener("click", () => { markReached(issue, meta); refresh(); });
  const un = card.querySelector('[data-action="undo"]');
  if (un) un.addEventListener("click", () => { unmarkReached(issue.number); refresh(); });

  return card;
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function refresh() {
  const reached = getReached();
  const cards = $("#cards");
  cards.innerHTML = "";

  const isReached = (i) => Object.prototype.hasOwnProperty.call(reached, String(i.number));

  let visible;
  if (CURRENT_FILTER === "done") {
    visible = ALL_OPEN.filter(isReached);
  } else {
    visible = ALL_OPEN.filter(i => !isReached(i));
    if (CURRENT_FILTER !== "all") {
      visible = visible.filter(i => {
        const m = parseMeta(i.body);
        return m && m.type === CURRENT_FILTER;
      });
    }
  }

  if (visible.length === 0) {
    cards.innerHTML = `<p class="loading">Nothing in this view.</p>`;
  } else {
    visible.forEach(i => {
      const c = renderCard(i, { done: CURRENT_FILTER === "done" });
      if (c) cards.appendChild(c);
    });
  }

  // ---- summary ----
  const toAction = ALL_OPEN.filter(i => !isReached(i)).length;
  const doneEntries = Object.values(reached);
  const reachedCount = doneEntries.length;
  const totalCycle = ALL_OPEN.length;  // total open universe; "reached" is local-only
  $("#stat-open").textContent = toAction;
  $("#stat-done").textContent = reachedCount;
  const rate = totalCycle ? Math.round(100 * reachedCount / totalCycle) : 0;
  $("#stat-rate").textContent = rate + "%";

  const c1Done = doneEntries.filter(d => d.type === "C1").length;
  const c2Done = doneEntries.filter(d => d.type === "C2").length;
  $("#stat-c1").textContent = c1Done;
  $("#stat-c2").textContent = c2Done;

  const thisWeek = isoWeekKey(new Date());
  const weekDone = doneEntries.filter(d => isoWeekKey(new Date(d.reachedAt)) === thisWeek).length;
  $("#stat-week").textContent = weekDone;

  drawChart(reached);
}

async function init() {
  $$("#filters button").forEach(b => b.addEventListener("click", () => {
    $$("#filters button").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    CURRENT_FILTER = b.dataset.filter;
    refresh();
  }));
  try {
    ALL_OPEN = await fetchIssues("open");
    refresh();
  } catch (e) {
    $("#cards").innerHTML = `<p class="loading">Failed to load: ${escapeHtml(e.message)}</p>`;
  }
}
init();
