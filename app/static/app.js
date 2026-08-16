const state = {
  results: [],
  selectedSymbol: null,
  liveDataAvailable: false,
};

const elements = {
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
  tabButtons: document.querySelectorAll(".tab-button"),
  tabPanels: document.querySelectorAll(".tab-panel"),
};

elements.scanButton.addEventListener("click", runScan);
elements.discoverButton.addEventListener("click", runDiscover);
elements.tabButtons.forEach((button) => {
  button.addEventListener("click", () => activateTab(button.dataset.tab));
});
elements.symbolsInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") runScan();
});

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
    const response = await fetch(`/scan?${params.toString()}`);
    const payload = await readJsonResponse(response);
    if (!response.ok) throw new Error(payload.detail || "Scan failed");

    state.results = payload.results;
    state.selectedSymbol = payload.results[0]?.symbol || null;
    renderSummary(payload);
    renderTable();
    renderDetail();
    drawScores();
  } catch (error) {
    elements.scanMeta.textContent = error.message;
    state.results = [];
    renderTable();
    renderEmptyDetail(error.message);
    drawScores();
    if (state.liveDataAvailable) renderModeNotice(`Live scanner request failed. ${error.message}`);
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
  const response = await fetch(`/scan?${params.toString()}`);
  const payload = await response.json();
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
  const params = new URLSearchParams({
    growth_min: elements.growthInput.value,
    period_days: elements.periodInput.value,
    limit: elements.limitInput.value,
    exclude_earnings: elements.excludeEarningsToggle.checked ? "true" : "false",
    demo: state.liveDataAvailable ? "false" : "true",
  });

  elements.discoverButton.disabled = true;
  elements.discoverMeta.textContent = "Discovering";

  try {
    const response = await fetch(`/discover?${params.toString()}`);
    const payload = await readJsonResponse(response);
    if (!response.ok) throw new Error(payload.detail || "Discovery failed");
    renderDiscovery(payload);
  } catch (error) {
    elements.discoverMeta.textContent = error.message;
    elements.discoverBody.innerHTML = `<tr><td class="empty-state" colspan="12">${error.message}</td></tr>`;
    if (state.liveDataAvailable) renderModeNotice(`Live discovery request failed. ${error.message}`);
  } finally {
    elements.discoverButton.disabled = false;
  }
}

async function runDiscoverFallback(reason) {
  const params = new URLSearchParams({
    growth_min: elements.growthInput.value,
    period_days: elements.periodInput.value,
    limit: elements.limitInput.value,
    exclude_earnings: elements.excludeEarningsToggle.checked ? "true" : "false",
    demo: "true",
  });
  const response = await fetch(`/discover?${params.toString()}`);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || reason);
  renderDiscovery(payload);
  renderModeNotice(`Live data request failed. Showing beta data. ${reason}`);
}

function renderDiscovery(payload) {
  elements.discoverMeta.textContent = `${payload.count} movers, ${displayMode(payload.data_mode)} mode`;
  if (!payload.results.length) {
    elements.discoverBody.innerHTML = `<tr><td class="empty-state" colspan="12">No movers matched this filter</td></tr>`;
    return;
  }

  elements.discoverBody.innerHTML = payload.results
    .map((item, index) => {
      return `
        <tr>
          <td>${index + 1}</td>
          <td><a class="symbol-link" href="${yahooFinanceUrl(item.symbol)}" target="_blank" rel="noopener noreferrer">${item.symbol}</a></td>
          <td><strong>${item.momentum_score.toFixed(1)}</strong></td>
          <td><span class="sector-pill">${item.sector}</span></td>
          <td>${formatPercent(item.return_2w)}</td>
          <td>${formatSignal(item.price_signal)}</td>
          <td>${formatRatio(item.volume_ratio_20d)}</td>
          <td>${formatPutCall(item.put_call_ratio, item.put_call_source)}</td>
          <td>${formatCallActivity(item)}</td>
          <td class="catalyst-cell">${item.catalyst}</td>
          <td>${formatSetup(item.setup)}</td>
          <td class="headline-cell">${formatPreferredTrade(item.preferred_contract)}</td>
        </tr>
      `;
    })
    .join("");
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
    elements.modeNotice.textContent = "Live MarketData mode: price growth and options activity are calculated from provider data.";
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

function formatRatio(value) {
  if (value === null || value === undefined) return "--";
  const className = value >= 1 ? "positive" : "negative";
  return `<span class="${className}">${value.toFixed(2)}x</span>`;
}

function formatPutCall(value, source) {
  if (value === null || value === undefined) return "--";
  const className = value <= 0.7 ? "positive" : value >= 1.2 ? "negative" : "";
  const label = source === "open_interest" ? "OI" : source === "demo" ? "beta" : "vol";
  return `<span class="${className}">${value.toFixed(2)}</span> <span class="subtle">${label}</span>`;
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

function formatPreferredTrade(contract) {
  if (!contract) return "--";
  const expiration = formatExpiration(contract.expiration);
  const delta = contract.delta === null || contract.delta === undefined ? "" : ` ~${Math.abs(contract.delta).toFixed(2)}Δ`;
  const strike = contract.strike === null || contract.strike === undefined ? "" : ` $${contract.strike.toFixed(0)} C`;
  return `${expiration}${strike}<span class="subtle block">${delta}</span>`;
}

function compactNumber(value) {
  if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}K`;
  return String(value);
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

function formatExpiration(expiration) {
  const value = String(expiration || "");
  if (value.startsWith("2027-01")) return "Jan 2027";
  if (!value) return "Expiration";
  return value.slice(0, 10);
}

async function init() {
  await checkHealth();
  runScan();
  runDiscover();
}

init();
