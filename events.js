// events.js — Events tab. Reads data/events.csv via the same GitHub Contents
// API pattern as the other CSVs. Filters: host, topic, time window, free-text.

(function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STATE = {
    rows: [],
    filters: { search: "", host: "", topic: "", window: "future" },
  };

  function withinWindow(dateStr, window) {
    if (!dateStr) return window === "all";
    const d = new Date(dateStr);
    if (isNaN(d)) return window === "all";
    const now = new Date();
    now.setHours(0, 0, 0, 0);
    if (window === "all") return true;
    if (window === "future") return d >= now;
    const days = window === "week" ? 7 : 30;
    const cutoff = new Date(now);
    cutoff.setDate(cutoff.getDate() + days);
    return d >= now && d <= cutoff;
  }

  function syncEventsHash() {
    const f = STATE.filters;
    const parts = [];
    for (const k of ["search", "host", "topic", "window"]) {
      if (f[k] && f[k] !== "future") parts.push(`${k}=${encodeURIComponent(f[k])}`);
    }
    const newHash = "#/events" + (parts.length ? "?" + parts.join("&") : "");
    if (window.location.hash !== newHash) {
      history.replaceState(null, "", newHash);
    }
  }

  function renderTable() {
    const rows = STATE.rows.filter(r => {
      const f = STATE.filters;
      if (f.host && r.host !== f.host) return false;
      if (f.topic && !((r.topic || "").includes(f.topic))) return false;
      if (!withinWindow(r.date_start, f.window)) return false;
      if (f.search) {
        const s = f.search.toLowerCase();
        const hay = (r.title + " " + r.speakers + " " + r.venue + " " + r.host).toLowerCase();
        if (!hay.includes(s)) return false;
      }
      return true;
    });
    // Sort by date ascending (future first), undated last
    rows.sort((a, b) => {
      const ad = a.date_start || "9999";
      const bd = b.date_start || "9999";
      return ad < bd ? -1 : ad > bd ? 1 : 0;
    });
    $("#events-count").textContent = `— ${rows.length.toLocaleString()} matching · ${STATE.rows.length.toLocaleString()} total`;
    // "shouldn't miss" = informal networking event (luncheon / breakfast /
    // cocktails / drinks / networking / AGM). These are where Felix actually
    // builds the relationships that drive referrals.
    const mustSeeRe = /\b(luncheon|brownbag|breakfast|cocktails?|drinks|networking|agm|reception)\b/i;
    const head = `
      <thead><tr>
        <th>Date</th><th>Title</th><th>Host</th><th>Topic</th>
        <th>Venue / City</th><th>Link</th>
      </tr></thead>`;
    const body = "<tbody>" + rows.map(r => {
      const isVirtual = (r.is_virtual || "").toString().toLowerCase() === "true";
      const cityChip = isVirtual ? "<span class='evt-virtual'>virtual</span>" : esc(r.city || "");
      const linkHtml = r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">register &rarr;</a>` : "";
      const mustSee = mustSeeRe.test(r.title || "");
      const star = mustSee ? `<span class="evt-must-see" title="High-value networking event">★</span> ` : "";
      const trClass = mustSee ? " class=\"evt-row-must-see\"" : "";
      return `<tr${trClass}>
        <td class="evt-date"><code>${esc(r.date_start || "—")}</code>${r.date_end ? `<br><small>&rarr; ${esc(r.date_end)}</small>` : ""}${r.time ? `<br><small class="muted-text">${esc(r.time)}</small>` : ""}</td>
        <td>${star}<strong>${esc(r.title)}</strong>${r.audience ? `<br><small class="muted-text">${esc(r.audience)}</small>` : ""}</td>
        <td><span class="evt-host">${esc(r.host)}</span></td>
        <td>${esc(r.topic || "")}</td>
        <td>${esc(r.venue || "")}${r.venue && (r.city || isVirtual) ? "<br>" : ""}<small>${cityChip}</small></td>
        <td>${linkHtml}</td>
      </tr>`;
    }).join("") + "</tbody>";
    $("#events-table").innerHTML = head + body;
  }

  function applyHashParams() {
    const h = window.location.hash || "";
    const q = (h.split("?")[1] || "").split("&");
    for (const p of q) {
      const [k, v] = p.split("=");
      if (!k) continue;
      const val = decodeURIComponent(v || "");
      if (k in STATE.filters) {
        STATE.filters[k] = val;
        const el = document.getElementById(`events-${k}`);
        if (el) el.value = val;
      }
    }
  }

  function wireFilters() {
    const onChange = (key) => (e) => {
      STATE.filters[key] = e.target.value;
      renderTable();
      syncEventsHash();
    };
    $("#events-search").addEventListener("input", onChange("search"));
    $("#events-host").addEventListener("change", onChange("host"));
    $("#events-topic").addEventListener("change", onChange("topic"));
    $("#events-window").addEventListener("change", onChange("window"));
  }

  function populateFilterOptions() {
    const hosts = new Set(STATE.rows.map(r => r.host).filter(Boolean));
    const topics = new Set();
    for (const r of STATE.rows) {
      for (const t of (r.topic || "").split(/[+,]/)) {
        const tt = t.trim();
        if (tt) topics.add(tt);
      }
    }
    const hostSel = $("#events-host");
    [...hosts].sort().forEach(h => {
      const opt = document.createElement("option");
      opt.value = h; opt.textContent = h;
      hostSel.appendChild(opt);
    });
    const topicSel = $("#events-topic");
    [...topics].sort().forEach(t => {
      const opt = document.createElement("option");
      opt.value = t; opt.textContent = t;
      topicSel.appendChild(opt);
    });
  }

  let _wired = false;
  async function show() {
    if (!STATE.rows.length) {
      try {
        const text = await K.fetchCsvText("data/events.csv");
        const parsed = K.parseCsv(text);
        STATE.rows = parsed.rows;
      } catch (e) {
        $("#events-table").innerHTML = `<tbody><tr><td><p class="loading">No events file yet, or fetch failed: ${esc(e.message)}</p></td></tr></tbody>`;
        return;
      }
      populateFilterOptions();
    }
    if (!_wired) {
      wireFilters();
      _wired = true;
    }
    applyHashParams();
    renderTable();
  }

  window.K = window.K || {};
  window.K.Events = { show };
})();
