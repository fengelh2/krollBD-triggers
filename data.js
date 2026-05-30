// data.js — shared data layer
// =====================================================================
// CSV access: GitHub Contents API (PAT-auth, same pattern as triggers).
// Large files (>1MB) need the blob endpoint; we fall back automatically.
// Data is cached in-memory per-page-load. window.K.* exposes everything.

(function () {
  const REPO = "fengelh2/krollBD";
  const PAT_KEY = "krollbd_pat";

  // ---- PAT (shared with triggers.js) ----
  function getPat() { return localStorage.getItem(PAT_KEY) || ""; }

  // ---- Auth headers for GitHub API ----
  function gh(headers) {
    const pat = getPat();
    return {
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(pat ? { "Authorization": `Bearer ${pat}` } : {}),
      ...(headers || {}),
    };
  }

  // ---- CSV fetch ----
  // Path is relative to repo root, e.g. "data/strategy_classification.csv".
  // Uses Contents API to handle private repos with PAT. For files > 1 MB the
  // Contents API returns empty content + a `download_url`; we follow that.
  // For files > 100 MB use the git/blobs endpoint (rare here).
  async function fetchCsvText(path) {
    // Cache-bust so the GitHub edge / browser never serves a stale CSV when
    // the data has been freshly committed (deep-scrape / weekly run / etc.).
    const bust = `t=${Date.now()}`;
    const url = `https://api.github.com/repos/${REPO}/contents/${encodeURI(path)}?${bust}`;
    const r = await fetch(url, {
      headers: gh(),
      cache: "no-store",
    });
    if (!r.ok) throw new Error(`GH contents ${path}: HTTP ${r.status}`);
    const meta = await r.json();
    if (meta.content) {
      // base64 inline (file <= 1MB)
      const b64 = (meta.content || "").replace(/\s/g, "");
      // atob → binary string → UTF-8
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return new TextDecoder("utf-8").decode(bytes);
    }
    // Large file: follow download_url which is a short-lived signed S3 URL
    // (no Authorization header allowed, would 400).
    if (meta.download_url) {
      // Cache-bust the download_url too (same reason as the Contents API call).
      const sep = meta.download_url.includes("?") ? "&" : "?";
      const r2 = await fetch(`${meta.download_url}${sep}t=${Date.now()}`, { cache: "no-store" });
      if (!r2.ok) throw new Error(`download_url ${path}: HTTP ${r2.status}`);
      return await r2.text();
    }
    throw new Error(`No content and no download_url for ${path}`);
  }

  // ---- CSV parser (handles quoted fields, embedded commas, escaped quotes) ----
  function parseCsv(text) {
    const rows = [];
    let cur = [];
    let field = "";
    let q = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) {
        if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
        else if (c === '"') { q = false; }
        else { field += c; }
      } else {
        if (c === '"') { q = true; }
        else if (c === ",") { cur.push(field); field = ""; }
        else if (c === "\r") { /* swallow */ }
        else if (c === "\n") { cur.push(field); rows.push(cur); cur = []; field = ""; }
        else { field += c; }
      }
    }
    if (field.length || cur.length) { cur.push(field); rows.push(cur); }
    if (!rows.length) return { headers: [], rows: [] };
    // Strip BOM from first header
    rows[0][0] = rows[0][0].replace(/^﻿/, "");
    const headers = rows[0];
    const out = [];
    for (let i = 1; i < rows.length; i++) {
      const r = rows[i];
      if (r.length === 1 && r[0] === "") continue;
      const o = {};
      for (let j = 0; j < headers.length; j++) o[headers[j]] = r[j] || "";
      out.push(o);
    }
    return { headers, rows: out };
  }

  function toCsv(rows, headers) {
    const esc = (v) => {
      v = v == null ? "" : String(v);
      if (/[",\n\r]/.test(v)) return `"${v.replace(/"/g, '""')}"`;
      return v;
    };
    const out = [headers.join(",")];
    for (const r of rows) out.push(headers.map(h => esc(r[h])).join(","));
    return out.join("\n");
  }

  // ---- the four datasets ----
  const PATHS = {
    classification: "data/strategy_classification.csv",
    corps:          "data/snapshots/sfc_t9_corps_latest.csv",
    individuals:    "data/snapshots/sfc_t9_individuals_latest.csv",
    pairs:          "data/snapshots/sfc_t9_corp_ros_latest.csv",
  };

  const CACHE = {};

  async function load(key) {
    if (CACHE[key]) return CACHE[key];
    const text = await fetchCsvText(PATHS[key]);
    const parsed = parseCsv(text);
    CACHE[key] = parsed;
    return parsed;
  }

  async function loadAll(onProgress) {
    const keys = Object.keys(PATHS);
    const results = {};
    let done = 0;
    await Promise.all(keys.map(async k => {
      try {
        results[k] = await load(k);
      } catch (e) {
        console.error("load fail", k, e);
        results[k] = { headers: [], rows: [], _error: e.message };
      }
      done++;
      if (onProgress) onProgress(done, keys.length, k);
    }));
    return results;
  }

  // ---- derived helpers ----
  function illiqRank(v) {
    return { high: 3, medium: 2, low: 1 }[String(v || "").toLowerCase()] || 0;
  }
  function isBdRelevant(row) {
    return illiqRank(row.illiquid_book_likelihood) >= 2;
  }
  function hasEmail(row) {
    return !!((row.emails_on_site || "").trim() || (row.ir_email || "").trim());
  }
  function hasGenericEmail(row) {
    return !!(row.generic_emails_on_site || "").trim();
  }
  function hasInferredEmail(row) {
    // From apply_email_patterns.py — pattern × SFC RO names. Medium-confidence
    // candidates (not verified per-address).
    return !!(row.inferred_named_emails || "").trim();
  }
  function hasAnyContact(row) {
    return hasEmail(row) || hasInferredEmail(row);
  }
  function hasWebsite(row) {
    return !!((row.website_url || "").trim());
  }
  function hasAum(row) {
    return !!((row.aum_usd_m || "").trim() || (row.aum_raw_string || "").trim());
  }
  function waBucket(row) {
    const v = String(row.website_accuracy || "").trim().toLowerCase();
    return v || "unknown";
  }

  window.K = window.K || {};
  Object.assign(window.K, {
    REPO,
    gh,
    fetchCsvText,
    parseCsv,
    toCsv,
    load,
    loadAll,
    PATHS,
    // derived
    illiqRank,
    isBdRelevant,
    hasEmail,
    hasGenericEmail,
    hasInferredEmail,
    hasAnyContact,
    hasWebsite,
    hasAum,
    waBucket,
  });
})();
