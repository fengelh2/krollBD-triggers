// krollBD-triggers dashboard
// Reads issues from GitHub API and renders them as cards.
// "Mark as reached out" hides locally via localStorage AND opens the GitHub issue
// in a new tab so the user can click Close. The summary uses the real closed
// issue count from GitHub as the source of truth.

const REPO = "fengelh2/krollBD-triggers";
const API = `https://api.github.com/repos/${REPO}/issues`;
const HIDDEN_KEY = "krollbd_hidden_ids";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let CURRENT_FILTER = "all";
let ALL_OPEN = [];
let CLOSED_COUNT = 0;

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

async function fetchOpenIssues() {
  // Paginate just in case
  const out = [];
  for (let page = 1; page <= 10; page++) {
    const r = await fetch(`${API}?state=open&per_page=100&page=${page}`,
      { headers: { "Accept": "application/vnd.github+json" } });
    if (!r.ok) throw new Error("GitHub API: " + r.status);
    const batch = await r.json();
    out.push(...batch.filter(i => !i.pull_request));
    if (batch.length < 100) break;
  }
  return out;
}

async function fetchClosedCount() {
  // Search API gives a total count cheaply
  const q = encodeURIComponent(`repo:${REPO} is:issue is:closed`);
  const r = await fetch(`https://api.github.com/search/issues?q=${q}&per_page=1`);
  if (!r.ok) return 0;
  const d = await r.json();
  return d.total_count || 0;
}

function copyToClipboard(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = "✓ copied";
    btn.classList.add("copied");
    setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 1500);
  });
}

function renderCard(issue) {
  const meta = parseMeta(issue.body);
  if (!meta) return null;  // skip issues without dash meta (e.g. manual ones)

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
    <p class="meta">CE ref <code>${escapeHtml(meta.ceref)}</code> · <a href="${meta.sfc_url}" target="_blank" rel="noopener">SFC register ↗</a> · <a href="${issue.html_url}" target="_blank" rel="noopener">GitHub issue ↗</a></p>
    ${meta.address ? `<p class="addr">${escapeHtml(meta.address)}</p>` : ""}

    ${rosList.length ? `
      <div class="ros">
        <span class="lbl">${meta.type === 'C2' ? 'ROs still on file' : 'Responsible Officers'}:</span>
        <ul>${rosList.map(r => `<li>${escapeHtml(r.name)} <span style="color:var(--muted)">(${escapeHtml(r.ceref)})</span></li>`).join("")}</ul>
      </div>` : ""}

    ${departed.length ? `
      <div class="ros">
        <span class="lbl" style="color:var(--purple)">Departed ROs (warm-lead candidates):</span>
        <ul>${departed.map(r => `<li>${escapeHtml(r.name)} <a href="https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(r.name)}" target="_blank" rel="noopener" style="font-size:11px">find on LinkedIn ↗</a></li>`).join("")}</ul>
      </div>` : ""}

    <div class="lookups">
      <a href="https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(meta.natural)}" target="_blank" rel="noopener">🔎 LinkedIn (firm)</a>
      ${meta.primary_ro ? `<a href="https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(meta.primary_ro + ' ' + meta.natural)}" target="_blank" rel="noopener">🔎 LinkedIn (${escapeHtml(meta.primary_ro)})</a>` : ""}
      <a href="https://www.google.com/search?q=${encodeURIComponent(meta.natural + ' hong kong contact email')}" target="_blank" rel="noopener">🔎 Google contact</a>
      <a href="https://duckduckgo.com/?q=${encodeURIComponent(meta.natural + ' hong kong asset management')}" target="_blank" rel="noopener">🔎 Find website</a>
    </div>

    <div class="email-block">
      <div class="email-row">
        <span class="label">Subject</span>
        <span class="value">${escapeHtml(meta.email_subject || "")}</span>
        <button class="btn copy" data-copy="subject">📋 copy subject</button>
      </div>
      <div class="email-row" style="margin-top:8px">
        <span class="label" style="align-self:flex-start;padding-top:4px">Body</span>
        <div style="flex:1">
          <pre class="email-body-text">${escapeHtml(meta.email_body || "")}</pre>
        </div>
      </div>
      <div class="actions" style="margin-top:8px;justify-content:flex-end">
        <button class="btn copy" data-copy="body">📋 copy body</button>
      </div>
    </div>

    <div class="actions">
      <button class="btn primary" data-action="reached-out">✓ Mark as reached out (hide + open issue to close)</button>
      <button class="btn" data-action="hide-only">👁️ Hide locally (don't close)</button>
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
    cards.innerHTML = `<p class="loading">No triggers in this view. 🎉</p>`;
  } else {
    visible.forEach(i => {
      const c = renderCard(i);
      if (c) cards.appendChild(c);
    });
  }

  const openVisible = ALL_OPEN.filter(i => !hidden.has(String(i.number))).length;
  const totalCycle = ALL_OPEN.length + CLOSED_COUNT;
  $("#stat-open").textContent = openVisible;
  $("#stat-done").textContent = CLOSED_COUNT;
  $("#stat-total").textContent = totalCycle;
  const rate = totalCycle ? Math.round(100 * CLOSED_COUNT / totalCycle) : 0;
  $("#stat-rate").textContent = rate + "%";
}

async function init() {
  $$("#filters button").forEach(b => b.addEventListener("click", () => {
    $$("#filters button").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    CURRENT_FILTER = b.dataset.filter;
    refresh();
  }));
  try {
    [ALL_OPEN, CLOSED_COUNT] = await Promise.all([fetchOpenIssues(), fetchClosedCount()]);
    refresh();
  } catch (e) {
    $("#cards").innerHTML = `<p class="loading">Failed to load: ${escapeHtml(e.message)}</p>`;
  }
}
init();
