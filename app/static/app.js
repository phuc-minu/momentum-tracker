const state = {
  results: [],
  selectedSymbol: null,
};

const elements = {
  apiStatus: document.querySelector("#apiStatus"),
  apiStatusText: document.querySelector("#apiStatusText"),
  symbolsInput: document.querySelector("#symbolsInput"),
  benchmarkInput: document.querySelector("#benchmarkInput"),
  countbackInput: document.querySelector("#countbackInput"),
  demoToggle: document.querySelector("#demoToggle"),
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
};

elements.scanButton.addEventListener("click", runScan);
elements.symbolsInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") runScan();
});

async function checkHealth() {
  try {
    const response = await fetch("/health");
    const payload = await response.json();
    elements.apiStatus.className = "status-dot ok";
    elements.apiStatusText.textContent = payload.marketdata_configured ? "Live ready" : "Demo ready";
  } catch {
    elements.apiStatus.className = "status-dot bad";
    elements.apiStatusText.textContent = "Offline";
  }
}

async function runScan() {
  const params = new URLSearchParams({
    symbols: elements.symbolsInput.value,
    benchmark: elements.benchmarkInput.value || "QQQ",
    countback: elements.countbackInput.value,
    demo: elements.demoToggle.checked ? "true" : "false",
  });

  elements.scanButton.disabled = true;
  elements.scanMeta.textContent = "Scanning";

  try {
    const response = await fetch(`/scan?${params.toString()}`);
    const payload = await response.json();
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
  } finally {
    elements.scanButton.disabled = false;
  }
}

function renderSummary(payload) {
  const average = payload.results.length
    ? payload.results.reduce((total, item) => total + item.score, 0) / payload.results.length
    : 0;
  elements.leaderCount.textContent = String(payload.count);
  elements.averageScore.textContent = payload.results.length ? average.toFixed(1) : "--";
  elements.topSetup.textContent = payload.results[0]?.setup || "--";
  elements.dataMode.textContent = payload.data_mode;
  elements.scanMeta.textContent = `${payload.count} symbols vs ${payload.benchmark}`;
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

checkHealth();
runScan();
