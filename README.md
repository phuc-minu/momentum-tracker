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

## Market Data Controls

FMP is the live provider for prices, candles, news, earnings, and symbol discovery. MarketData.app can be used as an options-only provider when `MARKETDATA_OPTIONS_API_KEY`, `MARKETDATA_TOKEN`, or the legacy `MARKETDATA_API_KEY` is configured. Live provider responses are cached on disk under `app/.cache/marketdata` by default. Historical dated candles are cached without expiry; latest quote, news, earnings, and options responses use short TTLs so repeated dashboard scans do not repull the same provider data.

Useful settings:

```powershell
$env:MARKETDATA_CACHE_DIR="C:\path\to\marketdata-cache"
$env:MARKETDATA_LATEST_CACHE_SECONDS="300"
$env:MARKETDATA_CANDLE_CACHE_SECONDS="900"
$env:MARKETDATA_REFERENCE_CACHE_SECONDS="3600"
$env:FMP_BASE_URL="https://financialmodelingprep.com/stable/"
$env:DISCOVERY_OPTIONS="true"
$env:MARKETDATA_OPTIONS_API_KEY="YOUR_MARKETDATA_APP_TOKEN"
$env:MARKETDATA_OPTIONS_DTE="30"
$env:MARKETDATA_OPTIONS_STRIKE_LIMIT="8"
```

`DISCOVERY_OPTIONS=true` enables options enrichment. MarketData.app options chains are preferred when a MarketData token is configured; otherwise the app can use an FMP-compatible options endpoint through `FMP_OPTIONS_CHAIN_PATH` or return options fields as unavailable.

## API

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

Azure runs the same `db refresh` job automatically at `22:30 UTC` Monday through Friday using the `momentumai-db-refresh` Container Apps Job. Scheduled-job cron expressions use UTC. The deployment script creates or updates the job whenever the required `database-url` and `fmp-api-key` Key Vault secrets are available. If the optional `marketdata-api-key` Key Vault secret exists, the job exposes it as `MARKETDATA_OPTIONS_API_KEY` for options enrichment. Discovery defaults to stocks priced above `$10` through `PHASE2_MIN_PRICE=10`.

Discovery mode and broader scheduled refreshes use `PHASE2_SYMBOL_SOURCE=fmp` by default to start from the FMP `company-screener` endpoint filtered by volume and price. Phase 2 then ranks that FMP screener pool by price movement over the last `PHASE2_PRICE_MOVEMENT_DAYS=30` calendar days and keeps `PHASE2_MOVER_LIMIT=50` symbols before momentum/options scoring. Use `DISCOVERY_SYMBOL_LIMIT` / `PHASE2_SYMBOL_LIMIT` to cap the FMP screener pool size and `DISCOVERY_MIN_AVERAGE_VOLUME` / `PHASE2_MIN_AVERAGE_VOLUME` to control the liquidity filter. Use `DISCOVERY_SYMBOL_SOURCE=static` only as a local fallback. Options and LLM enrichment should remain downstream-only for the top candidates.

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

## Next implementation step

Add persisted watchlists, scheduled scans, and optional GPT catalyst analysis over ranked candidates.
