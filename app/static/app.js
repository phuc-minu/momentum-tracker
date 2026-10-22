const state = {
  liveDataAvailable: false,
  discoveryRows: [],
  expandedDiscoverySymbols: new Set(),
  overextendedRows: [],
  biotechEvents: [],
  discoverySort: { key: "score", direction: "desc" },
};

const BIOTECH_STORAGE_KEY = "momentum:biotech-catalysts:v1";
const BIOTECH_CATALYST_WINDOW_DAYS = 30;
const DEFAULT_BIOTECH_EVENTS = [
  {
    id: "sample-mrna-cancer-vaccine",
    symbol: "MRNA",
    company: "Moderna",
    event_type: "Data presentation",
    catalyst_date: "2026-09-15",
    stage: "Phase 3",
    impact: "High",
    source: "",
    notes: "Cancer vaccine data window",
    sample: true,
  },
  {
    id: "sample-vktx-obesity-data",
    symbol: "VKTX",
    company: "Viking Therapeutics",
    event_type: "Trial readout",
    catalyst_date: "2026-10-01",
    stage: "Phase 2",
    impact: "High",
    source: "",
    notes: "Metabolic program update",
    sample: true,
  },
  {
    id: "sample-crsp-regulatory",
    symbol: "CRSP",
    company: "CRISPR Therapeutics",
    event_type: "Conference",
    catalyst_date: "2026-11-05",
    stage: "Commercial",
    impact: "Medium",
    source: "",
    notes: "Pipeline and launch update",
    sample: true,
  },
];

const DISCOVERY_SORT_CONFIG = {
  rank: { type: "number", direction: "asc", value: (item) => item.discovery_rank },
  symbol: { type: "text", direction: "asc", value: (item) => item.symbol },
  sector: { type: "text", direction: "asc", value: (item) => item.market_group || item.sector || "Unclassified" },
  price: { type: "number", direction: "desc", value: (item) => item.last_close },
  rsi: { type: "number", direction: "desc", value: (item) => item.rsi_14 },
  score: { type: "number", direction: "desc", value: (item) => item.momentum_score },
  growth: { type: "number", direction: "desc", value: (item) => item.return_2w },
  volume: { type: "number", direction: "desc", value: (item) => item.volume_ratio_20d },
  options: { type: "number", direction: "asc", value: (item) => item.put_call_ratio },
};

const elements = {
  landingScreen: document.querySelector("#landingScreen"),
  loginScreen: document.querySelector("#loginScreen"),
  loginForm: document.querySelector("#loginForm"),
  passwordInput: document.querySelector("#passwordInput"),
  loginButton: document.querySelector("#loginButton"),
  loginMessage: document.querySelector("#loginMessage"),
  dashboard: document.querySelector("#dashboard"),
  logoutButton: document.querySelector("#logoutButton"),
  apiStatus: document.querySelector("#apiStatus"),
  apiStatusText: document.querySelector("#apiStatusText"),
  modeNotice: document.querySelector("#modeNotice"),
  growthInput: document.querySelector("#growthInput"),
  periodInput: document.querySelector("#periodInput"),
  limitInput: document.querySelector("#limitInput"),
  excludeEarningsToggle: document.querySelector("#excludeEarningsToggle"),
  discoverButton: document.querySelector("#discoverButton"),
  discoverMeta: document.querySelector("#discoverMeta"),
  discoverBody: document.querySelector("#discoverBody"),
  overextendedMeta: document.querySelector("#overextendedMeta"),
  overextendedBody: document.querySelector("#overextendedBody"),
  biotechMeta: document.querySelector("#biotechMeta"),
  biotechBody: document.querySelector("#biotechBody"),
  growthColumnLabel: document.querySelector("#growthColumnLabel"),
  discoverySortButtons: document.querySelectorAll(".sort-button[data-sort-key]"),
  tabButtons: document.querySelectorAll(".tab-button"),
  tabPanels: document.querySelectorAll(".tab-panel"),
};

class AuthRequiredError extends Error {}
const DEFAULT_BUTTON_LABELS = new WeakMap();

elements.loginForm.addEventListener("submit", login);
window.addEventListener("hashchange", () => {
  if (!elements.dashboard.hidden) return;
  showPublicPage();
});
elements.logoutButton.addEventListener("click", logout);
elements.discoverButton.addEventListener("click", runDiscover);
elements.tabButtons.forEach((button) => {
  button.addEventListener("click", () => activateTab(button.dataset.tab));
});
elements.discoverySortButtons.forEach((button) => {
  button.addEventListener("click", () => setDiscoverySort(button.dataset.sortKey));
});
elements.discoverBody.addEventListener("click", (event) => {
  const toggle = event.target.closest(".row-toggle");
  if (!toggle || !elements.discoverBody.contains(toggle)) return;
  const symbol = toggle.dataset.symbol;
  if (state.expandedDiscoverySymbols.has(symbol)) state.expandedDiscoverySymbols.delete(symbol);
  else state.expandedDiscoverySymbols.add(symbol);
  const expanded = state.expandedDiscoverySymbols.has(symbol);
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.setAttribute("aria-label", `${expanded ? "Hide" : "Show"} details for ${symbol}`);
  const details = document.getElementById(toggle.getAttribute("aria-controls"));
  if (details) details.hidden = !expanded;
});
elements.periodInput.addEventListener("change", () => updateGrowthColumnHeader());
updateDiscoverySortIndicators();

function activateTab(tabId) {
  elements.tabButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tabId);
  });
  elements.tabPanels.forEach((panel) => {
    panel.classList.toggle("active", panel.id === tabId);
  });
}

async function checkHealth() {
  try {
    elements.apiStatus.className = "status-dot loading";
    elements.apiStatusText.textContent = "Checking";
    const response = await fetch("/health");
    const payload = await response.json();
    state.liveDataAvailable = Boolean(payload.fmp_configured);
    elements.apiStatus.className = "status-dot ok";
    elements.apiStatusText.textContent = state.liveDataAvailable ? "Live ready" : "Beta ready";
    renderModeNotice();
  } catch {
    state.liveDataAvailable = false;
    elements.apiStatus.className = "status-dot bad";
    elements.apiStatusText.textContent = "Offline";
    renderModeNotice("Unable to check live data status. Showing beta data when available.");
  }
}

async function checkAuth() {
  try {
    const response = await fetch("/auth/status", { credentials: "same-origin" });
    const payload = await readJsonResponse(response);
    if (payload.authenticated) {
      showDashboard();
      await loadDashboard();
    } else {
      showPublicPage();
    }
  } catch {
    showLogin("Unable to check login status.");
  }
}

async function login(event) {
  event.preventDefault();
  setButtonLoading(elements.loginButton, true, "Checking");
  elements.loginMessage.textContent = "Checking";

  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: elements.passwordInput.value }),
    });
    const payload = await readJsonResponse(response);
    if (!response.ok) throw new Error(payload.detail || "Login failed");
    elements.passwordInput.value = "";
    showDashboard();
    await loadDashboard();
  } catch (error) {
    elements.loginMessage.textContent = error.message;
  } finally {
    setButtonLoading(elements.loginButton, false);
  }
}

async function logout() {
  await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
  elements.passwordInput.value = "";
  showLogin();
}

function showLogin(message = "") {
  history.replaceState(null, "", "#login");
  elements.landingScreen.hidden = true;
  elements.dashboard.hidden = true;
  elements.loginScreen.hidden = false;
  elements.loginMessage.textContent = message;
  setButtonLoading(elements.discoverButton, false);
  elements.passwordInput.focus();
}

function showDashboard() {
  history.replaceState(null, "", window.location.pathname + window.location.search);
  elements.landingScreen.hidden = true;
  elements.loginScreen.hidden = true;
  elements.dashboard.hidden = false;
}

function showPublicPage() {
  if (window.location.hash === "#login") {
    showLogin();
    return;
  }
  elements.loginScreen.hidden = true;
  elements.dashboard.hidden = true;
  elements.landingScreen.hidden = false;
  const target = document.getElementById(window.location.hash.slice(1));
  if (target && elements.landingScreen.contains(target)) target.scrollIntoView();
}

async function runDiscover() {
  updateGrowthColumnHeader();
  setButtonLoading(elements.discoverButton, true, "Loading");
  elements.discoverMeta.textContent = "Loading candidates";

  try {
    await runPhase2Discovery();
  } catch (error) {
    if (error instanceof AuthRequiredError) return;
    if (state.liveDataAvailable) {
      try {
        await runLiveDiscoverFallback(error.message);
      } catch (fallbackError) {
        elements.discoverMeta.textContent = fallbackError.message;
        renderDiscoveryEmpty(fallbackError.message);
        renderModeNotice(`Phase 2 candidates unavailable. ${error.message}`);
      }
    } else {
      elements.discoverMeta.textContent = error.message;
      renderDiscoveryEmpty(error.message);
    }
  } finally {
    setButtonLoading(elements.discoverButton, false);
  }
}

async function runOverextended() {
  elements.overextendedMeta.textContent = "Loading pullback watch";
  try {
    const response = await fetch("/phase2/candidates?limit=200", { credentials: "same-origin" });
    const payload = await readJsonResponse(response);
    requireAuthenticated(response, payload);
    if (!response.ok) throw new Error(payload.detail || "Overextended candidates unavailable");

    const rows = payload.results
      .map(overextendedRow)
      .filter((item) => item.pullback_risk >= 45)
      .sort((left, right) => (
        right.pullback_risk - left.pullback_risk
        || (right.return_1m || 0) - (left.return_1m || 0)
        || left.symbol.localeCompare(right.symbol)
      ));

    state.overextendedRows = rows.map((item, index) => ({
      ...item,
      pullback_rank: index + 1,
    }));
    const refreshed = payload.results[0]?.scored_at ? ` refreshed ${formatDateTime(payload.results[0].scored_at)}` : "";
    elements.overextendedMeta.textContent = `${state.overextendedRows.length} pullback watches${refreshed}`;
    renderOverextendedRows();
    // Options paused until a replacement endpoint is available.
    // loadOverextendedOptions(state.overextendedRows);
  } catch (error) {
    if (error instanceof AuthRequiredError) return;
    state.overextendedRows = [];
    elements.overextendedMeta.textContent = error.message;
    elements.overextendedBody.innerHTML = `<tr><td class="empty-state" colspan="11">${error.message}</td></tr>`;
  }
}

async function loadOverextendedOptions(rows) {
  if (!rows.length) return;
  rows.forEach((row) => { row.options_loading = true; });
  renderOverextendedRows();
  try {
    const response = await fetch(`/options/three-month-volume?symbols=${encodeURIComponent(rows.map((row) => row.symbol).join(","))}`, { credentials: "same-origin" });
    const payload = await readJsonResponse(response);
    requireAuthenticated(response, payload);
    if (!response.ok) throw new Error("Options volume unavailable");
    const bySymbol = new Map(payload.results.map((row) => [row.symbol, row]));
    rows.forEach((row) => { row.options_3m = bySymbol.get(row.symbol); });
  } catch (error) {
    // Keep the pullback watch usable if the options provider is unavailable.
  } finally {
    rows.forEach((row) => { row.options_loading = false; });
    if (state.overextendedRows === rows) renderOverextendedRows();
  }
}

function formatThreeMonthOptions(item) {
  if (item.options_loading) return "Loading…";
  const options = item.options_3m;
  if (options?.total_volume === null || options?.total_volume === undefined) return "Unavailable";
  return `<span title="Calls: ${options.call_volume.toLocaleString()} · Puts: ${options.put_volume.toLocaleString()}">${options.total_volume.toLocaleString()}</span>`;
}

function setButtonLoading(button, isLoading, label = "Loading") {
  if (!DEFAULT_BUTTON_LABELS.has(button)) {
    DEFAULT_BUTTON_LABELS.set(button, button.innerHTML);
  }
  button.disabled = isLoading;
  button.setAttribute("aria-busy", isLoading ? "true" : "false");
  if (isLoading) {
    button.innerHTML = `<span class="spinner" aria-hidden="true"></span><span>${label}</span>`;
  } else {
    button.innerHTML = DEFAULT_BUTTON_LABELS.get(button);
  }
}

async function runPhase2Discovery() {
  const limit = Number(elements.limitInput.value);
  const params = new URLSearchParams({
    limit: "200",
    exclude_earnings: elements.excludeEarningsToggle.checked ? "true" : "false",
  });
  const response = await fetch(`/phase2/candidates?${params.toString()}`, { credentials: "same-origin" });
  const payload = await readJsonResponse(response);
  requireAuthenticated(response, payload);
  if (!response.ok) throw new Error(payload.detail || "Phase 2 candidates unavailable");

  const statusResponse = await fetch("/phase2/status", { credentials: "same-origin" });
  const statusPayload = await readJsonResponse(statusResponse);
  requireAuthenticated(statusResponse, statusPayload);

  renderPhase2Discovery({
    ...payload,
    limit,
    period_days: Number(elements.periodInput.value),
    growth_min: Number(elements.growthInput.value),
    refresh_status: statusResponse.ok ? statusPayload : null,
  });
}

async function runLiveDiscoverFallback(reason) {
  updateGrowthColumnHeader();
  const params = new URLSearchParams({
    growth_min: elements.growthInput.value,
    period_days: elements.periodInput.value,
    limit: elements.limitInput.value,
    exclude_earnings: elements.excludeEarningsToggle.checked ? "true" : "false",
    demo: "true",
  });
  const response = await fetch(`/discover?${params.toString()}`, { credentials: "same-origin" });
  const payload = await readJsonResponse(response);
  requireAuthenticated(response, payload);
  if (!response.ok) throw new Error(payload.detail || reason);
  renderDiscovery(payload);
  renderModeNotice(`Phase 2 candidates unavailable. Showing live fallback. ${reason}`);
}

function renderPhase2Discovery(payload) {
  updateGrowthColumnHeader(payload.period_days);
  const candidates = payload.results
    .map((item) => phase2ToDiscoveryRow(item, payload.period_days))
    .filter((item) => item.return_2w !== null && item.return_2w >= payload.growth_min)
    .slice(0, payload.limit);

  const status = payload.refresh_status;
  const statusText = status?.finished_at ? ` refreshed ${formatDateTime(status.finished_at)}` : "";
  elements.discoverMeta.textContent = `${candidates.length} candidates, PostgreSQL${statusText}`;
  const warning = latestRefreshWarning(status);
  if (warning) {
    renderModeNotice(warning.message);
  } else {
    renderModeNotice();
  }
  if (!candidates.length) {
    renderDiscoveryEmpty("No Phase 2 candidates matched this filter");
    return;
  }

  renderDiscoveryRows(candidates);
}

function compareDiscoveryRows(left, right) {
  const config = DISCOVERY_SORT_CONFIG[state.discoverySort.key];
  const primary = compareNullableValues(
    config.value(left),
    config.value(right),
    state.discoverySort.direction,
    config.type,
  );
  if (primary !== 0) return primary;

  if (state.discoverySort.key !== "score") {
    const scoreTieBreak = compareNullableValues(left.momentum_score, right.momentum_score, "desc", "number");
    if (scoreTieBreak !== 0) return scoreTieBreak;
  }
  const growthTieBreak = compareNullableValues(left.return_1m, right.return_1m, "desc", "number");
  if (growthTieBreak !== 0) return growthTieBreak;
  return left.symbol.localeCompare(right.symbol);
}

function compareNullableValues(left, right, direction, type) {
  const leftMissing = left === null || left === undefined || left === "" || (type === "number" && Number.isNaN(Number(left)));
  const rightMissing = right === null || right === undefined || right === "" || (type === "number" && Number.isNaN(Number(right)));
  if (leftMissing && rightMissing) return 0;
  if (leftMissing) return 1;
  if (rightMissing) return -1;
  const comparison = type === "number"
    ? Number(left) - Number(right)
    : String(left).localeCompare(String(right), undefined, { sensitivity: "base" });
  return direction === "asc" ? comparison : -comparison;
}

function compareDefaultDiscoveryRows(left, right) {
  const score = compareNullableValues(left.momentum_score, right.momentum_score, "desc", "number");
  if (score !== 0) return score;
  const growth = compareNullableValues(left.return_1m, right.return_1m, "desc", "number");
  if (growth !== 0) return growth;
  return left.symbol.localeCompare(right.symbol);
}

function setDiscoverySort(key) {
  if (state.discoverySort.key === key) {
    state.discoverySort.direction = state.discoverySort.direction === "desc" ? "asc" : "desc";
  } else {
    state.discoverySort = { key, direction: DISCOVERY_SORT_CONFIG[key].direction };
  }
  updateDiscoverySortIndicators();
  if (state.discoveryRows.length) renderSortedDiscoveryRows();
}

function updateDiscoverySortIndicators() {
  elements.discoverySortButtons.forEach((button) => {
    const active = button.dataset.sortKey === state.discoverySort.key;
    const header = button.closest("th");
    const indicator = button.querySelector(".sort-indicator");
    button.classList.toggle("active", active);
    if (active) {
      const ascending = state.discoverySort.direction === "asc";
      header.setAttribute("aria-sort", ascending ? "ascending" : "descending");
      indicator.textContent = ascending ? "↑" : "↓";
    } else {
      header.removeAttribute("aria-sort");
      indicator.textContent = "↕";
    }
  });
}

function renderDiscovery(payload) {
  updateGrowthColumnHeader(payload.period_days);
  elements.discoverMeta.textContent = `${payload.count} movers, ${displayMode(payload.data_mode)} mode`;
  if (!payload.results.length) {
    renderDiscoveryEmpty("No movers matched this filter");
    return;
  }

  renderDiscoveryRows(payload.results);
}

function renderDiscoveryRows(rows) {
  const rankBySymbol = new Map(
    [...rows]
      .sort(compareDefaultDiscoveryRows)
      .map((item, index) => [item.symbol, index + 1]),
  );
  state.discoveryRows = rows.map((item) => ({
    ...item,
    discovery_rank: rankBySymbol.get(item.symbol),
  }));
  renderSortedDiscoveryRows();
}

function renderDiscoveryEmpty(message) {
  state.discoveryRows = [];
  elements.discoverBody.innerHTML = `<tr><td class="empty-state" colspan="13">${escapeHtml(message)}</td></tr>`;
}

function renderSortedDiscoveryRows() {
  elements.discoverBody.innerHTML = [...state.discoveryRows]
    .sort(compareDiscoveryRows)
    .map((item, index) => {
      const symbol = String(item.symbol);
      const safeId = `details-${symbol.replace(/[^A-Za-z0-9_-]/g, "-")}-${index}`;
      const expanded = state.expandedDiscoverySymbols.has(symbol);
      const score = Number.isFinite(Number(item.momentum_score)) ? scoreHint(item) : "Momentum score unavailable.";
      return `
        <tr class="discover-row">
          <td class="col-toggle-cell"><button class="row-toggle" type="button" data-symbol="${escapeAttribute(symbol)}" aria-expanded="${expanded}" aria-controls="${safeId}" aria-label="${expanded ? "Hide" : "Show"} details for ${escapeAttribute(symbol)}"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3 6 5 5 5-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg></button></td>
          <td>${item.discovery_rank}</td>
          <td><a class="symbol-link" href="${yahooFinanceUrl(symbol)}" target="_blank" rel="noopener noreferrer">${escapeHtml(symbol)}</a></td>
          <td>${formatPrice(item.last_close)}</td>
          <td>${formatRsi(item.rsi_14)}</td>
          <td><strong title="${escapeAttribute(score)}" aria-label="${escapeAttribute(score)}">${Number(item.momentum_score).toFixed(1)}</strong></td>
          <td><span class="sector-pill">${item.market_group || item.sector || "Unclassified"}</span></td>
          <td>${formatPercent(item.return_2w)}</td>
          <td>${formatSignal(item.price_signal)}</td>
          <td>${formatRatio(item.volume_ratio_20d)}</td>
${/* Options columns paused; restore this template when an endpoint is available.
          <td>${formatPutCallVolumeRatio(item)}</td>
*/ ""}
          <td>${formatDateAdded(item.first_listed_at)}</td>
          <td>${formatPrice(item.first_listed_price)}</td>
          <td class="catalyst-cell">${formatCatalystSetup(item)}</td>
        </tr>
        <tr class="row-details" id="${safeId}"${expanded ? "" : " hidden"}><td colspan="13">${renderDiscoveryDetails(item)}</td></tr>
      `;
    })
    .join("");
}

function detailValue(value) {
  return value === null || value === undefined || value === "" ? "—" : escapeHtml(value);
}

function formattedDetail(formatter, value) {
  return value === null || value === undefined ? "—" : formatter(value);
}

function renderDiscoveryDetails(item) {
  const factors = item.factors || {};
  const factorRows = [
    ["1M return", formattedDetail(formatPercent, item.return_1m)],
    ["RSI (14)", formattedDetail(formatRsi, factors.rsi_14)],
    ["Distance to 50D SMA", formattedDetail(formatPercent, factors.distance_to_50d_sma)],
    ["Distance to 20D high", formattedDetail(formatPercent, factors.distance_to_20d_high)],
    ["Current / 20D volume", formattedDetail(formatRatio, item.volume_ratio_20d)],
  ].map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join("");
  const score = Number.isFinite(Number(item.momentum_score)) ? scoreHint(item) : "Momentum score unavailable.";
  const setup = item.setup ? escapeHtml(formatSetup(item.setup)) : "—";
  return `<div class="discovery-details"><div><h3>Score breakdown</h3><p>${escapeHtml(score)}</p><h3>Catalyst setup</h3><p>${detailValue(item.catalyst)}</p><p>Setup: ${setup}</p><p>Earnings checked: ${item.earnings_checked ? "Yes" : "No"}; near move: ${item.earnings_near_move ? "Yes" : "No"}; likely earnings-related: ${item.likely_earnings_related ? "Yes" : "No"}</p></div><div><h3>Key factors</h3><dl>${factorRows}</dl><h3>Chart</h3><div class="mini-chart-placeholder">Mini chart coming soon</div></div></div>`;
}

function phase2ToDiscoveryRow(item, periodDays) {
  const factors = item.factors || {};
  const growth = phase2Growth(factors, periodDays);
  return {
    symbol: item.symbol,
    sector: item.sector || "Unclassified",
    market_group: marketGroupLabel(item),
    momentum_score: item.score,
    setup: item.setup,
    price_signal: phase2PriceSignal(factors),
    volume_ratio_20d: factors.current_volume_ratio_20d ?? factors.volume_ratio_20d_vs_60d,
    rsi_14: factors.rsi_14,
    factors,
    return_1m: factors.return_1m,
    return_2w: growth,
    last_close: item.last_close,
    first_listed_at: item.first_listed_at,
    first_listed_price: item.first_listed_price,
    call_volume: item.call_volume,
    call_open_interest: item.call_open_interest,
    call_activity_ratio: item.call_activity_ratio,
    call_activity: item.call_activity || "unavailable",
    put_call_ratio: item.put_call_ratio,
    put_call_source: item.put_call_source || "unavailable",
    options_confirmation_score: item.options_confirmation_score,
    options_signal: item.options_signal || "Options unavailable",
    earnings_checked: Boolean(factors.earnings_checked),
    earnings_near_move: Boolean(factors.earnings_near_move),
    likely_earnings_related: Boolean(factors.likely_earnings_related),
    catalyst: factors.likely_earnings_related ? "likely earnings-related" : "PostgreSQL refresh",
  };
}

function overextendedRow(item) {
  const factors = item.factors || {};
  const risk = pullbackRiskScore(item, factors);
  return {
    symbol: item.symbol,
    last_close: item.last_close,
    setup: item.setup,
    return_1m: factors.return_1m,
    rsi_14: factors.rsi_14,
    distance_to_50d_sma: factors.distance_to_50d_sma,
    distance_to_20d_high: factors.distance_to_20d_high,
    current_volume_ratio_20d: factors.current_volume_ratio_20d ?? factors.volume_ratio_20d_vs_60d,
    pullback_risk: risk,
    reasons: pullbackReasons(item, factors),
  };
}

function pullbackRiskScore(item, factors) {
  let score = 0;
  if (item.setup === "extended") score += 30;
  if (factors.rsi_14 >= 80) score += 25;
  else if (factors.rsi_14 >= 70) score += 16;
  if (factors.distance_to_50d_sma !== null && factors.distance_to_50d_sma !== undefined) {
    score += clamp((factors.distance_to_50d_sma - 0.12) * 80, 0, 30);
  }
  if (factors.return_1m !== null && factors.return_1m !== undefined) {
    score += clamp((factors.return_1m - 0.20) * 35, 0, 18);
  }
  const volumeRatio = factors.current_volume_ratio_20d ?? factors.volume_ratio_20d_vs_60d;
  if (volumeRatio >= 2) score += 8;
  else if (volumeRatio >= 1.5) score += 4;
  if (factors.distance_to_20d_high !== null && factors.distance_to_20d_high !== undefined && factors.distance_to_20d_high <= -0.08) {
    score += 8;
  }
  return Math.min(100, Math.round(score));
}

function pullbackReasons(item, factors) {
  const reasons = [];
  if (item.setup === "extended") reasons.push("extended setup");
  if (factors.rsi_14 >= 80) reasons.push("RSI above 80");
  else if (factors.rsi_14 >= 70) reasons.push("RSI above 70");
  if (factors.distance_to_50d_sma >= 0.20) reasons.push("far above 50D");
  else if (factors.distance_to_50d_sma >= 0.12) reasons.push("stretched above 50D");
  if (factors.return_1m >= 0.30) reasons.push("large 1M move");
  const volumeRatio = factors.current_volume_ratio_20d ?? factors.volume_ratio_20d_vs_60d;
  if (volumeRatio >= 2) reasons.push("volume spike");
  if (factors.distance_to_20d_high <= -0.08) reasons.push("off recent high");
  return reasons.length ? reasons.join(", ") : "watch for mean reversion";
}

function renderOverextendedRows() {
  if (!state.overextendedRows.length) {
    elements.overextendedBody.innerHTML = `<tr><td class="empty-state" colspan="11">No overextended candidates matched the pullback criteria</td></tr>`;
    return;
  }

  elements.overextendedBody.innerHTML = state.overextendedRows
    .map((item) => `
      <tr class="${overextendedSeverityClass(item.pullback_risk)}">
        <td>${item.pullback_rank}</td>
        <td><a class="symbol-link" href="${yahooFinanceUrl(item.symbol)}" target="_blank" rel="noopener noreferrer">${item.symbol}</a></td>
        <td>${formatPrice(item.last_close)}</td>
        <td><strong class="${item.pullback_risk >= 80 ? "negative" : ""}">${item.pullback_risk}</strong></td>
        <td>${formatPercent(item.return_1m)}</td>
        <td>${formatRsi(item.rsi_14)}</td>
        <td>${formatPercent(item.distance_to_50d_sma)}</td>
        <td>${formatPercent(item.distance_to_20d_high)}</td>
        <td>${formatRatio(item.current_volume_ratio_20d)}</td>
${/* Options columns paused; restore this template when an endpoint is available.
        <td>${formatThreeMonthOptions(item)}</td>
        <td>${item.options_3m?.expiration || "—"}</td>
*/ ""}
        <td><span class="setup-pill">${item.setup}</span></td>
        <td class="reason-cell">${item.reasons}</td>
      </tr>
    `)
    .join("");
}

function overextendedSeverityClass(risk) {
  if (risk >= 90) return "overextended-extreme";
  if (risk >= 80) return "overextended-high";
  if (risk >= 65) return "overextended-medium";
  return "overextended-low";
}

function loadBiotechCatalysts() {
  try {
    const raw = localStorage.getItem(BIOTECH_STORAGE_KEY);
    const rows = raw ? JSON.parse(raw) : [];
    state.biotechEvents = Array.isArray(rows) && rows.length ? rows.filter(isBiotechEvent) : DEFAULT_BIOTECH_EVENTS;
  } catch {
    state.biotechEvents = DEFAULT_BIOTECH_EVENTS;
  }
  renderBiotechEvents();
}

function renderBiotechEvents() {
  const trackedRows = [...state.biotechEvents].sort(compareBiotechEvents);
  const rows = trackedRows.filter((item) => {
    const days = daysUntil(item.catalyst_date);
    return days >= 0 && days <= BIOTECH_CATALYST_WINDOW_DAYS;
  });
  elements.biotechMeta.textContent = `${rows.length} catalysts in next ${BIOTECH_CATALYST_WINDOW_DAYS} days, ${trackedRows.length} tracked`;

  if (!rows.length) {
    elements.biotechBody.innerHTML = `<tr><td class="empty-state" colspan="8">No biotech catalysts in the next ${BIOTECH_CATALYST_WINDOW_DAYS} days</td></tr>`;
    return;
  }

  elements.biotechBody.innerHTML = rows.map((item) => {
    const days = daysUntil(item.catalyst_date);
    return `
      <tr class="${catalystTimingClass(days)}">
        <td><a class="symbol-link" href="${yahooFinanceUrl(item.symbol)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.symbol)}</a></td>
        <td>${escapeHtml(item.company || "--")}</td>
        <td>${escapeHtml(item.event_type)}</td>
        <td>${formatCatalystDate(item.catalyst_date)}</td>
        <td><span class="catalyst-days">${formatDaysUntil(days)}</span></td>
        <td>${escapeHtml(item.stage)}</td>
        <td><span class="impact-pill ${impactClass(item.impact)}">${escapeHtml(item.impact)}</span></td>
        <td class="notes-cell">${formatBiotechNotes(item)}</td>
      </tr>
    `;
  }).join("");
}

function compareBiotechEvents(left, right) {
  const daySort = daysUntil(left.catalyst_date) - daysUntil(right.catalyst_date);
  if (daySort !== 0) return daySort;
  const impactSort = impactWeight(right.impact) - impactWeight(left.impact);
  if (impactSort !== 0) return impactSort;
  return left.symbol.localeCompare(right.symbol);
}

function isBiotechEvent(item) {
  return Boolean(item && item.id && item.symbol && item.event_type && item.catalyst_date);
}

function daysUntil(dateValue) {
  const date = new Date(`${dateValue}T00:00:00`);
  if (Number.isNaN(date.getTime())) return 9999;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((date.getTime() - today.getTime()) / 86400000);
}

function catalystTimingClass(days) {
  if (days < 0) return "catalyst-overdue";
  if (days <= 7) return "catalyst-imminent";
  if (days <= 30) return "catalyst-soon";
  return "catalyst-upcoming";
}

function formatDaysUntil(days) {
  if (days < 0) return `${Math.abs(days)}d overdue`;
  if (days === 0) return "Today";
  return `${days}d`;
}

function formatCatalystDate(dateValue) {
  const date = new Date(`${dateValue}T00:00:00`);
  if (Number.isNaN(date.getTime())) return escapeHtml(dateValue);
  return date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

function formatBiotechNotes(item) {
  const notes = item.notes ? escapeHtml(item.notes) : "--";
  if (!item.source) return notes;
  const source = escapeHtml(item.source);
  if (/^https?:\/\//i.test(item.source)) {
    return `${notes}<span class="subtle block"><a class="symbol-link" href="${escapeAttribute(item.source)}" target="_blank" rel="noopener noreferrer">Source</a></span>`;
  }
  return `${notes}<span class="subtle block">${source}</span>`;
}

function impactClass(impact) {
  if (impact === "High") return "impact-high";
  if (impact === "Medium") return "impact-medium";
  return "impact-low";
}

function impactWeight(impact) {
  if (impact === "High") return 3;
  if (impact === "Medium") return 2;
  return 1;
}

function marketGroupLabel(item) {
  if (item.sector && item.sector !== "Unclassified") return item.sector;
  return formatExchange(item.exchange);
}

function formatExchange(exchange) {
  const value = String(exchange || "").trim();
  if (!value || value === "configured") return "Configured";
  if (value === "demo") return "Demo";
  if (value === "benchmark") return "Benchmark";
  if (value === "NASDAQ") return "NASDAQ";
  if (value === "N") return "NYSE";
  if (value === "A") return "NYSE American";
  if (value === "P") return "NYSE Arca";
  if (value === "Z") return "Cboe BZX";
  if (value === "V") return "IEX";
  return value;
}

function phase2Growth(factors, periodDays) {
  return factors.return_1m;
}

function phase2PriceSignal(factors) {
  if (factors.distance_to_20d_high !== null && factors.distance_to_20d_high !== undefined && factors.distance_to_20d_high >= -0.005) {
    return "new 20-day high";
  }
  return "momentum continuation";
}

function updateGrowthColumnHeader(periodDays = Number(elements.periodInput.value)) {
  elements.growthColumnLabel.textContent = `${periodLabel(periodDays)} Growth`;
}

function periodLabel(periodDays) {
  if (periodDays === 10) return "2W";
  if (periodDays === 20 || periodDays === 30) return "30D";
  return `${periodDays}D`;
}

async function readJsonResponse(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

function requireAuthenticated(response, payload) {
  if (response.status !== 401) return;
  showLogin(payload.detail === "Login required" ? "Session expired. Log in again." : payload.detail || "Login required");
  throw new AuthRequiredError("Login required");
}

function latestRefreshWarning(status) {
  if (!status?.logs?.length) return null;
  return status.logs.find((log) => log.level === "warning") || null;
}

function renderModeNotice(message) {
  if (message) {
    elements.modeNotice.textContent = message;
    elements.modeNotice.className = "mode-notice warning";
    return;
  }
  if (state.liveDataAvailable) {
    elements.modeNotice.textContent = "Phase 2 mode: discovery reads precomputed PostgreSQL candidates from live FMP refreshes.";
    elements.modeNotice.className = "mode-notice live";
  } else {
    elements.modeNotice.textContent = "Beta data mode: live FMP data is not configured yet, so displayed growth and options values are generated preview data.";
    elements.modeNotice.className = "mode-notice beta";
  }
}

function formatPercent(value) {
  if (value === null || value === undefined) return "--";
  const className = value >= 0 ? "positive" : "negative";
  return `<span class="${className}">${(value * 100).toFixed(1)}%</span>`;
}

function formatPrice(value) {
  if (value === null || value === undefined) return "--";
  return `$${value.toFixed(2)}`;
}

function formatRatio(value) {
  if (value === null || value === undefined) return "--";
  const className = value >= 1 ? "positive" : "negative";
  return `<span class="${className}">${value.toFixed(2)}x</span>`;
}

function formatRsi(value) {
  if (value === null || value === undefined) return "--";
  const className = value >= 70 ? "negative" : value <= 30 ? "positive" : "";
  return `<span class="${className}">${Number(value).toFixed(1)}</span>`;
}

function displayMode(mode) {
  return mode === "demo" ? "beta" : mode;
}

function formatCallActivity(item) {
  if (!item.call_activity || item.call_activity === "unavailable") return "--";
  const className = item.call_activity === "unusually elevated" ? "positive" : "";
  const volume = item.call_volume ? ` ${compactNumber(item.call_volume)}` : "";
  return `<span class="${className}">${formatActivity(item.call_activity)}</span><span class="subtle block">${volume}</span>`;
}

function formatPutCallVolumeRatio(item) {
  if (item.put_call_ratio === null || item.put_call_ratio === undefined) return "--";
  const ratio = Number(item.put_call_ratio);
  const className = ratio <= 0.7 ? "positive" : ratio >= 1.2 ? "negative" : "";
  const hint = escapeAttribute(putCallHint(item));
  return `<strong class="${className}" title="${hint}" aria-label="${hint}">${ratio.toFixed(2)}</strong><span class="subtle block" title="${hint}">Vol ratio</span>`;
}

function scoreHint(item) {
  return [
    `Momentum score ${item.momentum_score.toFixed(1)} out of 100.`,
    "Ranks price momentum using growth, price signal, volume, and options confirmation.",
    `Setup: ${item.setup}.`,
    `Signal: ${formatSignal(item.price_signal)}.`,
    `Growth: ${(item.return_2w * 100).toFixed(1)}%.`,
    item.rsi_14 !== null && item.rsi_14 !== undefined ? `RSI: ${Number(item.rsi_14).toFixed(1)}.` : "",
  ].filter(Boolean).join(" ");
}

function putCallHint(item) {
  const pieces = [
    `Put/call volume ratio ${Number(item.put_call_ratio).toFixed(2)}.`,
    "Calculated as 10-DTE put volume divided by 10-DTE call volume.",
  ];
  if (item.call_volume) {
    pieces.push(`Call volume: ${compactNumber(item.call_volume)}.`);
  }
  if (item.put_call_source) {
    pieces.push(`Source: ${item.put_call_source}.`);
  }
  return pieces.join(" ");
}

function formatCatalystSetup(item) {
  const catalyst = item.catalyst || "--";
  return `${catalyst}<span class="subtle block">${formatSetup(item.setup)}</span>`;
}

function compactNumber(value) {
  if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}K`;
  return String(value);
}

function formatDateAdded(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toISOString().slice(0, 10);
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function yahooFinanceUrl(symbol) {
  return `https://finance.yahoo.com/quote/${encodeURIComponent(symbol)}`;
}

function formatSignal(signal) {
  if (signal === "new 50-day high") return "50D high";
  if (signal === "new 20-day high") return "20D high";
  if (signal === "momentum continuation") return "Continuation";
  return signal || "--";
}

function formatSetup(setup) {
  if (setup === "Momentum surge") return "Surge";
  if (setup === "Early breakout") return "Early breakout";
  if (setup === "Breakout watch") return "Breakout watch";
  return setup || "--";
}

function formatActivity(activity) {
  if (activity === "unusually elevated") return "Unusual";
  if (activity === "elevated") return "Elevated";
  return activity;
}

function formatOptionsSignal(signal) {
  if (signal === "Confirmed bullish momentum") return "Confirmed";
  if (signal === "Options confirming") return "Confirming";
  if (signal === "Options neutral") return "Neutral";
  if (signal === "Speculative call activity") return "Speculative";
  if (signal === "Options not confirming") return "Not confirming";
  if (signal === "Options weak") return "Weak";
  return "Unavailable";
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function escapeAttribute(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function escapeHtml(value) {
  return escapeAttribute(value);
}

async function init() {
  await checkAuth();
}

async function loadDashboard() {
  await checkHealth();
  updateGrowthColumnHeader();
  runDiscover();
  runOverextended();
  loadBiotechCatalysts();
}

init();
