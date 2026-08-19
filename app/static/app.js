const state = {
  results: [],
  selectedSymbol: null,
  liveDataAvailable: false,
};

const elements = {
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
  symbolsInput: document.querySelector("#symbolsInput"),
  benchmarkInput: document.querySelector("#benchmarkInput"),
  countbackInput: document.querySelector("#countbackInput"),
  scanButton: document.querySelector("#scanButton"),
  leaderCount: document.querySelector("#leaderCount"),
  averageScore: document.querySelector("#averageScore"),
  topSetup: document.querySelector("#topSetup"),
  dataMode: document.querySelector("#dataMode"),
  scanMeta: document.querySelector("#scanMeta"),
  resultsBody: document.querySelector("#resultsBody"),
  detailTitle: document.querySelector("#detailTitle"),
  detailGrade: document.querySelector("#detailGrade"),
  factorGrid: document.querySelector("#factorGrid"),
  notesList: document.querySelector("#notesList"),
  scoreCanvas: document.querySelector("#scoreCanvas"),
  growthInput: document.querySelector("#growthInput"),
  periodInput: document.querySelector("#periodInput"),
  limitInput: document.querySelector("#limitInput"),
  excludeEarningsToggle: document.querySelector("#excludeEarningsToggle"),
  discoverButton: document.querySelector("#discoverButton"),
  discoverMeta: document.querySelector("#discoverMeta"),
  discoverBody: document.querySelector("#discoverBody"),
  growthColumnHeader: document.querySelector("#growthColumnHeader"),
  tabButtons: document.querySelectorAll(".tab-button"),
  tabPanels: document.querySelectorAll(".tab-panel"),
};

class AuthRequiredError extends Error {}

elements.loginForm.addEventListener("submit", login);
elements.logoutButton.addEventListener("click", logout);
elements.scanButton.addEventListener("click", runScan);
elements.discoverButton.addEventListener("click", runDiscover);
elements.tabButtons.forEach((button) => {
  button.addEventListener("click", () => activateTab(button.dataset.tab));
});
elements.symbolsInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") runScan();
});
elements.periodInput.addEventListener("change", () => updateGrowthColumnHeader());

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
    const response = await fetch("/health");
    const payload = await response.json();
    state.liveDataAvailable = Boolean(payload.marketdata_configured);
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
      showLogin();
    }
  } catch {
    showLogin("Unable to check login status.");
  }
}

async function login(event) {
  event.preventDefault();
  elements.loginButton.disabled = true;
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
    elements.loginButton.disabled = false;
  }
}

async function logout() {
  await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
  state.results = [];
  elements.passwordInput.value = "";
  showLogin();
}

function showLogin(message = "") {
  elements.dashboard.hidden = true;
  elements.loginScreen.hidden = false;
  elements.loginMessage.textContent = message;
  elements.discoverButton.disabled = false;
  elements.scanButton.disabled = false;
  elements.passwordInput.focus();
}

function showDashboard() {
  elements.loginScreen.hidden = true;
  elements.dashboard.hidden = false;
}

async function runScan() {
  const params = new URLSearchParams({
    symbols: elements.symbolsInput.value,
    benchmark: elements.benchmarkInput.value || "QQQ",
    countback: elements.countbackInput.value,
    demo: state.liveDataAvailable ? "false" : "true",
  });

  elements.scanButton.disabled = true;
  elements.scanMeta.textContent = "Scanning";

  try {
    const response = await fetch(`/scan?${params.toString()}`, { credentials: "same-origin" });
    const payload = await readJsonResponse(response);
    requireAuthenticated(response, payload);
    if (!response.ok) throw new Error(payload.detail || "Scan failed");

    state.results = payload.results;
    state.selectedSymbol = payload.results[0]?.symbol || null;
    renderSummary(payload);
    renderTable();
    renderDetail();
    drawScores();
  } catch (error) {
    if (error instanceof AuthRequiredError) return;
    if (state.liveDataAvailable) {
      try {
        await runScanFallback(error.message);
      } catch (fallbackError) {
        elements.scanMeta.textContent = fallbackError.message;
        state.results = [];
        renderTable();
        renderEmptyDetail(fallbackError.message);
        drawScores();
        renderModeNotice(`Live scanner request failed. ${error.message}`);
      }
    } else {
      elements.scanMeta.textContent = error.message;
      state.results = [];
      renderTable();
      renderEmptyDetail(error.message);
      drawScores();
    }
  } finally {
    elements.scanButton.disabled = false;
  }
}

async function runScanFallback(reason) {
  const params = new URLSearchParams({
    symbols: elements.symbolsInput.value,
    benchmark: elements.benchmarkInput.value || "QQQ",
    countback: elements.countbackInput.value,
    demo: "true",
  });
  const response = await fetch(`/scan?${params.toString()}`, { credentials: "same-origin" });
  const payload = await readJsonResponse(response);
  requireAuthenticated(response, payload);
  if (!response.ok) throw new Error(payload.detail || reason);
  state.results = payload.results;
  state.selectedSymbol = payload.results[0]?.symbol || null;
  renderSummary(payload);
  renderTable();
  renderDetail();
  drawScores();
  renderModeNotice(`Live scanner request failed. Showing beta data. ${reason}`);
}

async function runDiscover() {
  updateGrowthColumnHeader();
  elements.discoverButton.disabled = true;
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
        elements.discoverBody.innerHTML = `<tr><td class="empty-state" colspan="13">${fallbackError.message}</td></tr>`;
        renderModeNotice(`Phase 2 candidates unavailable. ${error.message}`);
      }
    } else {
      elements.discoverMeta.textContent = error.message;
      elements.discoverBody.innerHTML = `<tr><td class="empty-state" colspan="13">${error.message}</td></tr>`;
    }
  } finally {
    elements.discoverButton.disabled = false;
  }
}

async function runPhase2Discovery() {
  const limit = Number(elements.limitInput.value);
  const response = await fetch("/phase2/candidates?limit=200", { credentials: "same-origin" });
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
    .sort(compareDiscoveryRows)
    .slice(0, payload.limit);

  const status = payload.refresh_status;
  const statusText = status?.finished_at ? ` refreshed ${formatDateTime(status.finished_at)}` : "";
  elements.discoverMeta.textContent = `${candidates.length} candidates, PostgreSQL${statusText}`;
  if (!candidates.length) {
    elements.discoverBody.innerHTML = `<tr><td class="empty-state" colspan="13">No Phase 2 candidates matched this filter</td></tr>`;
    return;
  }

  renderDiscoveryRows(candidates);
}

function compareDiscoveryRows(left, right) {
  const scoreDelta = right.momentum_score - left.momentum_score;
  if (scoreDelta !== 0) return scoreDelta;
  return (right.return_2w ?? Number.NEGATIVE_INFINITY) - (left.return_2w ?? Number.NEGATIVE_INFINITY);
}

function renderDiscovery(payload) {
  updateGrowthColumnHeader(payload.period_days);
  elements.discoverMeta.textContent = `${payload.count} movers, ${displayMode(payload.data_mode)} mode`;
  if (!payload.results.length) {
    elements.discoverBody.innerHTML = `<tr><td class="empty-state" colspan="13">No movers matched this filter</td></tr>`;
    return;
  }

  renderDiscoveryRows(payload.results);
}

function renderDiscoveryRows(rows) {
  elements.discoverBody.innerHTML = rows
    .map((item, index) => {
      return `
        <tr>
          <td>${index + 1}</td>
          <td><a class="symbol-link" href="${yahooFinanceUrl(item.symbol)}" target="_blank" rel="noopener noreferrer">${item.symbol}</a></td>
          <td>${formatPrice(item.last_close)}</td>
          <td><strong title="${escapeAttribute(scoreHint(item))}" aria-label="${escapeAttribute(scoreHint(item))}">${item.momentum_score.toFixed(1)}</strong></td>
          <td><span class="sector-pill">${item.market_group || item.sector || "Unclassified"}</span></td>
          <td>${formatPercent(item.return_2w)}</td>
          <td>${formatRsi(item.rsi_14)}</td>
          <td>${formatSignal(item.price_signal)}</td>
          <td>${formatRatio(item.volume_ratio_20d)}</td>
          <td>${formatPutCall(item.put_call_ratio, item.put_call_source)}</td>
          <td>${formatCallActivity(item)}</td>
          <td>${formatOptionsConfirmation(item)}</td>
          <td class="catalyst-cell">${formatCatalystSetup(item)}</td>
        </tr>
      `;
    })
    .join("");
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
    volume_ratio_20d: factors.volume_ratio_20d_vs_60d,
    rsi_14: factors.rsi_14,
    return_2w: growth,
    last_close: item.last_close,
    put_call_ratio: item.put_call_ratio,
    put_call_source: item.put_call_source || "unavailable",
    call_volume: item.call_volume,
    call_open_interest: item.call_open_interest,
    call_activity_ratio: item.call_activity_ratio,
    call_activity: item.call_activity || "unavailable",
    options_confirmation_score: item.options_confirmation_score,
    options_signal: item.options_signal || "Options unavailable",
    catalyst: "PostgreSQL refresh",
  };
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
  if (periodDays === 60) return factors.return_3m;
  if (periodDays === 126) return factors.return_6m;
  if (periodDays === 252) return factors.return_12m;
  return factors.return_1m;
}

function phase2PriceSignal(factors) {
  if (factors.distance_to_20d_high !== null && factors.distance_to_20d_high !== undefined && factors.distance_to_20d_high >= -0.005) {
    return "new 20-day high";
  }
  return "momentum continuation";
}

function updateGrowthColumnHeader(periodDays = Number(elements.periodInput.value)) {
  elements.growthColumnHeader.textContent = `${periodLabel(periodDays)} Growth`;
}

function periodLabel(periodDays) {
  if (periodDays === 10) return "2W";
  if (periodDays === 20) return "1M";
  if (periodDays === 60) return "3M";
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

function renderSummary(payload) {
  const average = payload.results.length
    ? payload.results.reduce((total, item) => total + item.score, 0) / payload.results.length
    : 0;
  elements.leaderCount.textContent = String(payload.count);
  elements.averageScore.textContent = payload.results.length ? average.toFixed(1) : "--";
  elements.topSetup.textContent = payload.results[0]?.setup || "--";
  elements.dataMode.textContent = displayMode(payload.data_mode);
  elements.scanMeta.textContent = `${payload.count} symbols vs ${payload.benchmark}`;
}

function renderModeNotice(message) {
  if (message) {
    elements.modeNotice.textContent = message;
    elements.modeNotice.className = "mode-notice warning";
    return;
  }
  if (state.liveDataAvailable) {
    elements.modeNotice.textContent = "Phase 2 mode: discovery reads precomputed PostgreSQL candidates; manual scanner still uses live MarketData.";
    elements.modeNotice.className = "mode-notice live";
  } else {
    elements.modeNotice.textContent = "Beta data mode: live MarketData is not configured yet, so displayed growth and options values are generated preview data.";
    elements.modeNotice.className = "mode-notice beta";
  }
}

function renderTable() {
  if (!state.results.length) {
    elements.resultsBody.innerHTML = `<tr><td class="empty-state" colspan="8">No scan results</td></tr>`;
    return;
  }

  elements.resultsBody.innerHTML = state.results
    .map((item, index) => {
      const factors = item.factors;
      const selected = item.symbol === state.selectedSymbol ? "selected" : "";
      return `
        <tr class="${selected}" data-symbol="${item.symbol}">
          <td>${index + 1}</td>
          <td><strong>${item.symbol}</strong></td>
          <td>
            <div class="score-cell">
              <strong>${item.score.toFixed(1)}</strong>
              <span class="score-bar"><span style="width:${item.score}%"></span></span>
            </div>
          </td>
          <td><span class="grade-pill">${item.grade}</span></td>
          <td><span class="setup-pill">${item.setup}</span></td>
          <td>${formatPercent(factors.relative_strength_3m)}</td>
          <td>${formatPercent(factors.distance_to_50d_sma)}</td>
          <td>${formatRatio(factors.volume_ratio_20d_vs_60d)}</td>
        </tr>
      `;
    })
    .join("");

  document.querySelectorAll("#resultsBody tr[data-symbol]").forEach((row) => {
    row.addEventListener("click", () => {
      state.selectedSymbol = row.dataset.symbol;
      renderTable();
      renderDetail();
      drawScores();
    });
  });
}

function renderDetail() {
  const item = state.results.find((candidate) => candidate.symbol === state.selectedSymbol);
  if (!item) {
    renderEmptyDetail("Select a ranked symbol");
    return;
  }

  elements.detailTitle.textContent = `${item.symbol} ${item.setup}`;
  elements.detailGrade.textContent = item.grade;
  elements.factorGrid.innerHTML = [
    ["1M Return", formatPercent(item.factors.return_1m)],
    ["3M Return", formatPercent(item.factors.return_3m)],
    ["6M Return", formatPercent(item.factors.return_6m)],
    ["12M Return", formatPercent(item.factors.return_12m)],
    ["3M RS", formatPercent(item.factors.relative_strength_3m)],
    ["20D High", formatPercent(item.factors.distance_to_20d_high)],
    ["50D SMA", formatPercent(item.factors.distance_to_50d_sma)],
    ["200D SMA", formatPercent(item.factors.distance_to_200d_sma)],
    ["RSI", formatRsi(item.factors.rsi_14)],
  ]
    .map(([label, value]) => `<div class="factor"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  elements.notesList.innerHTML = item.notes.map((note) => `<li>${note}</li>`).join("");
}

function renderEmptyDetail(message) {
  elements.detailTitle.textContent = "Details";
  elements.detailGrade.textContent = "--";
  elements.factorGrid.innerHTML = "";
  elements.notesList.innerHTML = `<li>${message}</li>`;
}

function drawScores() {
  const canvas = elements.scoreCanvas;
  const context = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#fbfcfe";
  context.fillRect(0, 0, width, height);

  if (!state.results.length) return;

  const gap = 12;
  const topPad = 18;
  const bottomPad = 34;
  const leftPad = 34;
  const barWidth = (width - leftPad - gap * (state.results.length + 1)) / state.results.length;
  const chartHeight = height - topPad - bottomPad;

  context.strokeStyle = "#d9e0ea";
  context.beginPath();
  context.moveTo(leftPad, height - bottomPad);
  context.lineTo(width - 10, height - bottomPad);
  context.stroke();

  state.results.forEach((item, index) => {
    const x = leftPad + gap + index * (barWidth + gap);
    const barHeight = Math.max(4, (item.score / 100) * chartHeight);
    const y = height - bottomPad - barHeight;
    context.fillStyle = item.symbol === state.selectedSymbol ? "#087f8c" : "#94a3b8";
    context.fillRect(x, y, barWidth, barHeight);
    context.fillStyle = "#344054";
    context.font = "12px Segoe UI, sans-serif";
    context.textAlign = "center";
    context.fillText(item.symbol, x + barWidth / 2, height - 12);
  });
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

function formatPutCall(value, source) {
  if (value === null || value === undefined) return "--";
  const className = value <= 0.7 ? "positive" : value >= 1.2 ? "negative" : "";
  const label = source === "open_interest" ? "OI" : source === "demo" ? "beta" : source === "volume" ? "vol" : "";
  return `<span class="${className}">${value.toFixed(2)}</span>${label ? ` <span class="subtle">${label}</span>` : ""}`;
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

function formatOptionsConfirmation(item) {
  if (item.options_confirmation_score === null || item.options_confirmation_score === undefined) return "--";
  const className = item.options_confirmation_score >= 65 ? "positive" : item.options_confirmation_score < 50 ? "negative" : "";
  const hint = escapeAttribute(optionsHint(item));
  return `<strong class="${className}" title="${hint}" aria-label="${hint}">${item.options_confirmation_score.toFixed(0)}</strong><span class="subtle block" title="${hint}">${formatOptionsSignal(item.options_signal)}</span>`;
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

function optionsHint(item) {
  const pieces = [
    `Options confirmation score ${item.options_confirmation_score.toFixed(0)} out of 100.`,
    `${item.options_signal}.`,
    "Uses put/call ratio, call volume, call volume vs open interest, and preferred contract delta.",
  ];
  if (item.put_call_ratio !== null && item.put_call_ratio !== undefined) {
    pieces.push(`Put/call: ${item.put_call_ratio.toFixed(2)}.`);
  }
  if (item.call_volume) {
    pieces.push(`Call volume: ${compactNumber(item.call_volume)}.`);
  }
  if (item.call_activity_ratio !== null && item.call_activity_ratio !== undefined) {
    pieces.push(`Call volume/OI: ${item.call_activity_ratio.toFixed(2)}x.`);
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

function escapeAttribute(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function init() {
  await checkAuth();
}

async function loadDashboard() {
  await checkHealth();
  updateGrowthColumnHeader();
  runScan();
  runDiscover();
}

init();
