# Stock Momentum AI — Azure MVP

This is the Azure deployment scaffold for a momentum-first investing agent.

## App features

- FastAPI service with health, universe, and scan endpoints
- FMP daily candle ingestion when `FMP_API_KEY` is configured
- Deterministic demo mode for local scanner development without API calls
- Momentum score, grade, and setup classification for breakout, pullback, extended, base, or weak charts
- Optional benchmark-relative 3-month strength and explainable scoring factors

## Run locally

```powershell
cd app
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload
```

Try the deterministic scanner:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/scan?symbols=NVDA,MSFT,AAPL,AVGO&demo=true"
```

Use live FMP candles:

```powershell
$env:FMP_API_KEY="YOUR_FMP_TOKEN"
Invoke-RestMethod "http://127.0.0.1:8000/scan?symbols=NVDA,MSFT,AAPL,AVGO"
```

## FMP Controls

FMP is the live provider for prices, candles, news, earnings, symbol discovery, and optional options enrichment. Live provider responses are cached on disk under `app/.cache/fmp` by default. Historical dated candles are cached without expiry; latest quote, news, earnings, and options responses use short TTLs so repeated dashboard scans do not repull the same provider data.

Useful settings:

```powershell
$env:FMP_CACHE_DIR="C:\path\to\fmp-cache"
$env:FMP_LATEST_CACHE_SECONDS="300"
$env:FMP_CANDLE_CACHE_SECONDS="900"
$env:FMP_REFERENCE_CACHE_SECONDS="3600"
$env:FMP_BASE_URL="https://financialmodelingprep.com/stable/"
$env:DISCOVERY_OPTIONS="true"
$env:FMP_OPTIONS_CHAIN_PATH="options-chain"
$env:FMP_POSITIONS_SUMMARY_ENABLED="false"
```

`DISCOVERY_OPTIONS=true` enables options enrichment. The app can use an FMP-compatible options endpoint through `FMP_OPTIONS_CHAIN_PATH`; otherwise options fields are returned as unavailable. The put/call ratio is calculated from option-chain volume only: total put volume divided by total call volume.

## API

Options columns in Discovery and Overextended, and the Overextended options request, are temporarily commented out pending a replacement options endpoint. The retained Overextended implementation provides **3M Options Vol** and **Options Expiry**. Volume is the sum of call and put contract volumes across all strikes for the available expiration nearest three calendar months from today (within 15 days); hover the total for the call/put breakdown. This requires `FMP_OPTIONS_CHAIN_PATH` to return dated option-chain rows with volume. Missing or incomplete volume is displayed as unavailable, and institutional holdings are never substituted for trading volume. Options load independently from the saved stock scores through the authenticated `/options/three-month-volume?symbols=AAPL,MSFT` endpoint.

- `GET /health` returns service and API-key configuration status
- `GET /universe` returns the configured default universe
- `GET /scan?symbols=NVDA,MSFT&demo=true` ranks symbols
- `GET /phase2/candidates` returns precomputed PostgreSQL-backed momentum candidates
- `GET /phase2/status` returns the latest Phase 2 refresh job status
- `POST /scan` accepts JSON:

```json
{
  "symbols": ["NVDA", "MSFT", "AAPL"],
  "benchmark": "",
  "countback": 60,
  "demo": true
}
```

## Phase 2 precomputed screener

Phase 2 moves broad market discovery out of the click path:

```text
FMP daily candles -> PostgreSQL daily_bars -> momentum_scores -> top candidates -> FastAPI reads
```

Configure PostgreSQL:

```powershell
$env:DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/DBNAME"
```

Run a safe demo refresh:

```powershell
cd app
..\.venv\Scripts\python.exe phase2_refresh.py --demo --top 50
```

Run a controlled live refresh:

```powershell
$env:PHASE2_SYMBOL_SOURCE="fmp"
$env:PHASE2_SYMBOL_LIMIT="200"
$env:PHASE2_MOVER_LIMIT="50"
$env:PHASE2_PRICE_MOVEMENT_DAYS="30"
$env:PHASE2_MIN_AVERAGE_VOLUME="3000000"
$env:PHASE2_VOLUME_PREFILTER_DAYS="20"
$env:PHASE2_FMP_CONCURRENCY="2"
..\.venv\Scripts\python.exe phase2_refresh.py --top 50
```

Azure runs the same `db refresh` job automatically at `22:30 UTC` Monday through Friday using the `momentumai-db-refresh` Container Apps Job. Scheduled-job cron expressions use UTC. The deployment script creates or updates the job whenever the required `database-url` and `fmp-api-key` Key Vault secrets are available. Discovery defaults to stocks priced above `$10` through `PHASE2_MIN_PRICE=10`.

Discovery mode and broader scheduled refreshes use `PHASE2_SYMBOL_SOURCE=fmp` by default to start from the FMP `company-screener` endpoint filtered by average volume and price. The FMP request uses `avgVolumeMoreThan` for the liquidity threshold and `priceMoreThan` for the price floor. Phase 2 then ranks that FMP screener pool by price movement over the last `PHASE2_PRICE_MOVEMENT_DAYS=30` calendar days and keeps `PHASE2_MOVER_LIMIT=50` symbols before momentum/options scoring. Use `DISCOVERY_SYMBOL_LIMIT` / `PHASE2_SYMBOL_LIMIT` to cap the FMP screener pool size and `DISCOVERY_MIN_AVERAGE_VOLUME` / `PHASE2_MIN_AVERAGE_VOLUME` to control the liquidity filter. Use `DISCOVERY_SYMBOL_SOURCE=static` only as a local fallback. Options and LLM enrichment should remain downstream-only for the top candidates.

Momentum scoring is intentionally short-window: scheduled refreshes fetch `PHASE2_COUNTBACK=60` bars, rank 30-calendar-day price change, and use 50D SMA only when enough bars are available. The scanner no longer requires or scores against 200D SMA / one-year history.

## Resources

- Azure Container Apps (scale-to-zero, max 2 replicas)
- Azure Container Registry (Basic)
- Azure Key Vault
- User-assigned managed identity
- Log Analytics workspace

## Before deployment

From the VS Code terminal authenticated to the intended Azure subscription:

```powershell
az account show
$env:FMP_API_KEY="YOUR_FMP_TOKEN"
$env:OPENAI_API_KEY="YOUR_OPENAI_API_KEY"
./deploy.ps1
```

Bash/Linux:

```bash
az account show
export FMP_API_KEY='YOUR_FMP_TOKEN'
export OPENAI_API_KEY='YOUR_OPENAI_API_KEY'
./deploy.sh
```

Default deployment:
- Resource group: `rg-tech-momentum-ai`
- Region: `westus2`
- Prefix: `momentumai`

## Verify

The deploy script prints the public URL. Open `/health`; both configuration flags should be true.

## Test

```powershell
cd app
python -m pytest
```

## Local GitHub issue agent (Windows PowerShell 7)

The agent in `scripts/issue-agent.ps1` polls open issues labeled `ai-ready` when explicitly run with `-Repository phuc-minu/momentum-tracker` and `-RepoRoot <repo-path>` after `gh auth login`; use `-Once` for a single poll. Issue text is untrusted input, and a trusted collaborator must apply the label; edits made after labeling require the label to be removed and applied again. Claude is restricted to read-only tools for planning and post-Codex review, and project/local Claude settings and MCP servers are excluded from those host-side runs. Codex runs in a disposable Linux Docker container with only the per-issue worktree mounted; GitHub, Azure, and host-profile credentials are not mounted. The worker requires a dedicated AI-only API key in the current user's `ISSUE_AGENT_CODEX_API_KEY` environment variable; Codex's shell environment excludes the key and its workspace-write command sandbox has network access disabled. Tests run in separate disposable Linux Docker containers with networking disabled and source mounted read-only; the `live_site` integration test is excluded. State updates are atomic, with ignored local scratch files and redacted logs; native commands have bounded timeouts, and timed-out worker/test containers are removed. Failed phases retry with backoff and report a final failure once. Re-applying `ai-ready` after a final issue-processing failure starts a fresh worktree and branch. Terminal deployment failures require the explicit `-Mode retry-deployment -IssueNumber <n>` command. The workflow pauses for human review after creating and pushing an explanatory PR. This source change does not install or register the agent.

```powershell
pwsh -File .\scripts\issue-agent.ps1 -Mode poll -Repository phuc-minu/momentum-tracker -RepoRoot (Get-Location) -Once
```

The installer is `scripts/install-issue-agent.ps1`; it is not run automatically and should be run only after this workflow has been reviewed and merged, because it clones the repository's default branch. Before installation, create a dedicated AI-only API key and save it as the current user's `ISSUE_AGENT_CODEX_API_KEY` environment variable through Windows Environment Variables; do not reuse a key that grants access to host accounts or other services. It requires Docker Desktop (or another Docker Engine) in Linux-container mode and registers a current-user, non-overlapping, restart-configured at-logon task that starts immediately. The Codex worker image is built on its first run; the host Codex CLI is not required. To update an existing install, stop the running task first with `Stop-ScheduledTask -TaskName 'TechMomentumAI-IssueAgent'`, then rerun the installer to fast-forward its clone. Each new trusted label application after a final issue-processing failure creates a fresh worktree and branch, preserving the prior failed attempt. After a human merges a reviewed PR, deployment validates the recorded PR's head and base branches, tests the latest default-branch commit containing that merge, builds the `app/` Docker context in ACR, updates the existing `momentumai-api`, and verifies the ready revision is running the expected image before checking `/health`. It also updates the image on the existing `momentumai-db-refresh` job if present, without changing that job's schedule, secrets, or other infrastructure. Deploying the latest default-branch commit prevents an older merge from rolling production back. It does not call `deploy.ps1`, provision infrastructure, or write Key Vault secrets. Terminal deployment failures do not retry indefinitely; after correcting the cause, run `pwsh -File .\scripts\issue-agent.ps1 -Mode retry-deployment -IssueNumber <n>`. The task requires the local Claude CLI, `gh` authentication, Azure CLI authentication, Docker, the dedicated API key, and network connectivity for image builds and Azure operations.

## Next implementation step

Add persisted watchlists, scheduled scans, and optional GPT catalyst analysis over ranked candidates.

Discovery **Date Added** is the first recorded list appearance in UTC, persisted on the symbol even if it leaves and later returns. Tracking starts on the first database refresh after this feature is installed; older first-appearance dates cannot be reconstructed from the latest-score-only database. Unknown dates display as an em dash. The refresh initializes the idempotent `symbols.first_listed_at` schema migration automatically.

Discovery **Price When Added** stores the closing price used at the first recorded list appearance. This value is preserved across subsequent refreshes and re-entry. The refresh adds `symbols.first_listed_price` and backfills existing symbols only when the retained score timestamp exactly matches their first appearance; otherwise historical entry prices remain unknown.
