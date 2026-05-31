// ── State ──
let _report = null;
let _markdown = "";
let _duration = 0;

// ── Init ──
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".tab").forEach(btn => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
  document.querySelectorAll(".filter-chip").forEach(chip => {
    chip.addEventListener("click", () => filterSuggestions(chip.dataset.sev));
  });
  ["repo", "pr-number", "pr-url"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("keydown", e => {
      if (e.key === "Enter") runAnalysis();
    });
  });
});

// ── URL Parser ──
function parseURL() {
  const raw = document.getElementById("pr-url").value.trim();
  if (!raw) return;
  // Match: https://github.com/owner/repo/pull/123 (optional trailing path/query)
  const m = raw.match(/github\.com\/([^\/]+)\/([^\/]+)\/pull\/(\d+)/);
  if (m) {
    document.getElementById("repo").value = `${m[1]}/${m[2]}`;
    document.getElementById("pr-number").value = m[3];
  }
}

function fillDemo() {
  document.getElementById("repo").value = "HelicasECoode42/ai-pr-review";
  document.getElementById("pr-number").value = "1";
  document.getElementById("pr-url").value = "";
  document.getElementById("language").value = "zh";
  document.getElementById("use-ai").value = "0";
  hideError();
  hideResults();
}

// ── API ──
async function runAnalysis() {
  const repo = document.getElementById("repo").value.trim();
  const prNumber = parseInt(document.getElementById("pr-number").value, 10);
  const language = document.getElementById("language").value;
  const useAi = document.getElementById("use-ai").value === "1";

  if (!repo || !prNumber) {
    showStatus("Please fill in repository and PR number.", "error");
    return;
  }

  setLoading(true, "Fetching PR data…");
  hideError();
  hideResults();
  showStatus("", "");
  document.getElementById("analyze-btn").disabled = true;

  try {
    const steps = ["Fetching PR data…", "Scanning risk rules…", "Running AI review…", "Building report…"];
    let stepIdx = 0;
    const stepTimer = setInterval(() => {
      if (stepIdx < steps.length - 1) stepIdx++;
      document.getElementById("loading-text").textContent = steps[stepIdx];
    }, 1200);

    const t0 = performance.now();
    const resp = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo, pr_number: prNumber, language, use_ai: useAi }),
    });

    clearInterval(stepTimer);
    const elapsed = ((performance.now() - t0) / 1000).toFixed(1);

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }

    const data = await resp.json();
    _report = data.report;
    _markdown = data.markdown;
    _duration = data.duration_seconds || parseFloat(elapsed);

    renderResults();
    setLoading(false);
    showStatus(`✓ Done in ${_duration}s`, "ok");
  } catch (err) {
    setLoading(false);
    showError(err.message || "Unknown error");
    showStatus("Analysis failed", "error");
  } finally {
    document.getElementById("analyze-btn").disabled = false;
  }
}

// ── Render ──
function renderResults() {
  const r = _report;
  if (!r) return;

  // Overview
  const riskLevel = (r.risk_level || "low").toUpperCase();
  document.getElementById("m-risk").querySelector(".value").textContent = riskLevel;
  setMetricClass("m-risk", riskLevel === "LOW" ? "good" : riskLevel === "CRITICAL" ? "bad" : "warn");

  const fileCount = (r.files || []).length;
  document.getElementById("m-files").querySelector(".value").textContent = fileCount;

  const adds = (r.files || []).reduce((s, f) => s + (f.additions || 0), 0);
  const dels = (r.files || []).reduce((s, f) => s + (f.deletions || 0), 0);
  document.getElementById("m-changes").querySelector(".value").textContent = `+${adds}/-${dels}`;

  document.getElementById("m-ai").querySelector(".value").textContent = r.used_ai ? "✓" : "✗";
  setMetricClass("m-ai", r.used_ai ? "good" : "");

  const conf = (r.report_confidence || "normal");
  document.getElementById("m-confidence").querySelector(".value").textContent = conf;
  setMetricClass("m-confidence", conf === "normal" ? "good" : conf === "failed" ? "bad" : "warn");

  document.getElementById("m-duration").querySelector(".value").textContent = `${_duration}s`;

  // GitHub link
  const ghLink = document.getElementById("gh-link");
  if (r.pr && r.pr.html_url) {
    ghLink.href = r.pr.html_url;
    ghLink.classList.remove("hidden");
  } else {
    ghLink.classList.add("hidden");
  }

  // Suggestions
  renderSuggestions((r.suggestions || []));
  renderCompleteness((r.completeness || []));
  renderMarkdown(_markdown);

  document.getElementById("results-section").classList.remove("hidden");
  switchTab("suggestions");
}

function setMetricClass(id, cls) {
  const el = document.getElementById(id);
  el.classList.remove("good", "warn", "bad");
  if (cls) el.classList.add(cls);
}

// ── Suggestions ──
function renderSuggestions(suggestions) {
  const list = document.getElementById("suggestions-list");
  const noop = document.getElementById("no-suggestions");
  if (!suggestions.length) {
    list.innerHTML = "";
    noop.classList.remove("hidden");
    return;
  }
  noop.classList.add("hidden");

  const sevOrder = { critical: 0, high: 1, medium: 2, low: 3 };
  const sorted = [...suggestions].sort((a, b) => (sevOrder[a.severity] || 4) - (sevOrder[b.severity] || 4));

  list.innerHTML = sorted.map((s, i) => `
    <div class="suggestion-card" data-sev="${s.severity}" data-idx="${i}">
      <div class="header">
        <span class="sev-badge sev-${s.severity}">${s.severity}</span>
        <span class="conf-badge">${Math.round(s.confidence * 100)}% confidence</span>
        <a class="file-link" href="${buildBlobUrl(s.file_path, s.line)}" target="_blank" rel="noopener">
          ${s.file_path}${s.line ? ":" + s.line : ""}
        </a>
        <button class="copy-btn" onclick="copySuggestion(${i})">📋 Copy comment</button>
      </div>
      <div class="title">${escHtml(s.title)}</div>
      <div class="reason">${escHtml(s.reason || "")}</div>
      ${s.recommendation ? `<div class="recommendation">💡 ${escHtml(s.recommendation)}</div>` : ""}
    </div>
  `).join("");

  _suggestionsCache = sorted;
}
let _suggestionsCache = [];

function filterSuggestions(sev) {
  document.querySelectorAll(".filter-chip").forEach(c => c.classList.remove("active"));
  document.querySelector(`.filter-chip[data-sev="${sev}"]`)?.classList.add("active");

  document.querySelectorAll(".suggestion-card").forEach(card => {
    if (sev === "all" || card.dataset.sev === sev) {
      card.style.display = "";
    } else {
      card.style.display = "none";
    }
  });
}

function copySuggestion(idx) {
  const s = _suggestionsCache[idx];
  if (!s) return;
  const text = `**${s.severity}**: ${s.title}\n\n> ${s.reason}\n\nRecommendation: ${s.recommendation}`;
  navigator.clipboard.writeText(text).then(() => {
    showStatus("Comment copied!", "ok");
    setTimeout(() => showStatus("", ""), 2000);
  });
}

// ── Completeness ──
function renderCompleteness(items) {
  const container = document.getElementById("completeness-list");
  if (!items.length) {
    container.innerHTML = '<p class="muted">No completeness data.</p>';
    return;
  }
  const iconMap = { success: "✅", partial: "⚠️", failed: "❌", skipped: "➖" };
  const rows = items.map(i => `
    <tr>
      <td>${escHtml(i.item)}</td>
      <td>${iconMap[i.status] || ""} ${escHtml(i.status)}</td>
      <td>${escHtml(i.detail)}</td>
    </tr>
  `).join("");
  container.innerHTML = `
    <table>
      <thead><tr><th>Step</th><th>Status</th><th>Detail</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── Markdown ──
function renderMarkdown(md) {
  const el = document.getElementById("report-html");
  if (typeof marked !== "undefined") {
    marked.setOptions({ breaks: true });
    el.innerHTML = marked.parse(md);
  } else {
    el.innerHTML = `<pre>${escHtml(md)}</pre>`;
  }
}

// ── Utils ──
function buildBlobUrl(filePath, line) {
  const r = _report;
  if (!r || !r.pr || !r.pr.head_sha || !r.pr.repo) return "#";
  let url = `https://github.com/${r.pr.repo}/blob/${r.pr.head_sha}/${filePath}`;
  if (line) url += `#L${line}`;
  return url;
}

function escHtml(s) {
  if (!s) return "";
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ── Tab switching ──
function switchTab(name) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
  document.querySelector(`.tab[data-tab="${name}"]`)?.classList.add("active");
  document.getElementById(`panel-${name}`)?.classList.add("active");
}

// ── Actions ──
function downloadJSON() {
  if (!_report) return;
  const blob = new Blob([JSON.stringify(_report, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `pr-review-${_report.pr?.number || "unknown"}.json`;
  a.click();
}

function copyReport() {
  if (!_markdown) return;
  navigator.clipboard.writeText(_markdown).then(() => {
    showStatus("Report copied to clipboard!", "ok");
    setTimeout(() => showStatus("", ""), 2500);
  });
}

// ── UI helpers ──
function setLoading(on, text) {
  document.getElementById("loading-section").classList.toggle("hidden", !on);
  document.getElementById("loading-text").textContent = text || "Analyzing…";
}

function showStatus(msg, type) {
  const el = document.getElementById("status-msg");
  el.textContent = msg;
  el.classList.remove("hidden");
  el.style.color = type === "error" ? "#f85149" : type === "ok" ? "#3fb950" : "#8b949e";
  if (!msg) el.classList.add("hidden");
}

function showError(msg) {
  const el = document.getElementById("error-msg");
  // Map raw errors to user-friendly messages
  const map = [
    [/rate limit/i, "GitHub API rate limit hit. Wait a minute and try again, or set a GITHUB_TOKEN."],
    [/not found/i, "PR or repository not found. Check the URL — the repo may be private or the PR number may not exist."],
    [/authentication failed/i, "GitHub token is invalid or expired. Check your .env GITHUB_TOKEN."],
    [/access denied/i, "Access denied. The repository may be private or the token lacks permissions."],
    [/timeout/i, "Request timed out. The GitHub API may be slow, try again."],
  ];
  let friendly = msg;
  for (const [re, replacement] of map) {
    if (re.test(msg)) { friendly = replacement; break; }
  }
  el.textContent = friendly;
  document.getElementById("error-section").classList.remove("hidden");
}

function hideError() {
  document.getElementById("error-section").classList.add("hidden");
}

function hideResults() {
  document.getElementById("results-section").classList.add("hidden");
}

// ── History ──
let _historyCache = [];

async function loadHistory() {
  const list = document.getElementById("history-list");
  const summary = document.getElementById("history-summary");
  const noop = document.getElementById("no-history");
  list.innerHTML = '<p class="muted">Loading history…</p>';
  summary.innerHTML = "";

  try {
    const resp = await fetch("/api/history");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    _historyCache = await resp.json();

    if (!_historyCache.length) {
      list.innerHTML = "";
      noop.classList.remove("hidden");
      return;
    }
    noop.classList.add("hidden");

    // Trend summary
    const levels = _historyCache.map(e => e.risk_level);
    const recent = levels.slice(0, 5);
    const totalReviews = _historyCache.length;
    const totalSuggestions = _historyCache.reduce((s, e) => s + e.suggestions_count, 0);
    const avgSuggestions = totalReviews ? (totalSuggestions / totalReviews).toFixed(1) : 0;

    // Risk trend: count risk levels
    const riskCounts = {};
    levels.forEach(l => { riskCounts[l] = (riskCounts[l] || 0) + 1; });
    const riskEmoji = { low: "🟢", medium: "🟡", high: "🟠", critical: "🔴" };
    const riskLabel = { low: "低", medium: "中", high: "高", critical: "严重" };

    let trendHtml = `<div class="history-summary-grid">`;
    trendHtml += `<div class="hs-item"><span class="hs-value">${totalReviews}</span><span class="hs-label">总审查次数</span></div>`;
    trendHtml += `<div class="hs-item"><span class="hs-value">${totalSuggestions}</span><span class="hs-label">总建议数</span></div>`;
    trendHtml += `<div class="hs-item"><span class="hs-value">${avgSuggestions}</span><span class="hs-label">平均建议/次</span></div>`;
    trendHtml += `<div class="hs-item"><span class="hs-value">`;
    for (const [lev, cnt] of Object.entries(riskCounts).sort()) {
      trendHtml += `${riskEmoji[lev] || ""}${cnt} `;
    }
    trendHtml += `</span><span class="hs-label">风险分布</span></div>`;
    trendHtml += `</div>`;
    summary.innerHTML = trendHtml;

    // Table
    const rows = _historyCache.map((e, i) => {
      const date = (e.analyzed_at || "").replace(/(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/, "$1-$2-$3 $4:$5");
      return `
        <tr class="history-row" onclick="loadHistoryEntry(${i})" style="cursor:pointer">
          <td><a href="${escHtml(e.html_url || '#')}" onclick="event.stopPropagation()" target="_blank" rel="noopener">#${e.pr_number}</a></td>
          <td>${escHtml(e.title || "")}</td>
          <td>${date}</td>
          <td><span class="sev-badge sev-${e.risk_level}">${riskLabel[e.risk_level] || e.risk_level}</span></td>
          <td>${e.files_count} files</td>
          <td>+${e.additions}/-${e.deletions}</td>
          <td>${e.suggestions_count} suggestions</td>
          <td>${e.used_ai ? "✅" : "⏭️"}</td>
        </tr>`;
    }).join("");

    list.innerHTML = `
      <div class="table-scroll">
        <table class="history-table">
          <thead>
            <tr>
              <th>PR</th>
              <th>Title</th>
              <th>Date</th>
              <th>Risk</th>
              <th>Files</th>
              <th>Changes</th>
              <th>Issues</th>
              <th>AI</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  } catch (err) {
    list.innerHTML = `<p class="error">Failed to load history: ${escHtml(err.message)}</p>`;
  }
}

async function loadHistoryEntry(idx) {
  const entry = _historyCache[idx];
  if (!entry) return;

  setLoading(true, "Loading historical report…");
  hideError();

  try {
    const resp = await fetch(`/api/history/${entry.id}_full`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const report = await resp.json();

    _report = report;
    _markdown = "";
    _duration = 0;

    renderResults();
    setLoading(false);
    showStatus(`Loaded review for PR #${entry.pr_number}`, "ok");
    // Scroll to results
    document.getElementById("results-section").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    setLoading(false);
    showError("Failed to load historical report: " + err.message);
  }
}

// Override switchTab to auto-load history
const _originalSwitchTab = switchTab;
switchTab = function(name) {
  _originalSwitchTab(name);
  if (name === "history") {
    loadHistory();
  }
};
