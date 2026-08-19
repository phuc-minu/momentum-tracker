# Stock Momentum AI — Azure MVP

This is the Azure deployment scaffold for a momentum-first investing agent.

## App features

- FastAPI service with health, universe, and scan endpoints
- MarketData.app daily candle ingestion when `MARKETDATA_API_KEY` is configured
- Deterministic demo mode for local scanner development without API calls
- Momentum score, grade, and setup classification for breakout, pullback, extended, base, or weak charts
- QQQ-relative 3-month strength and explainable scoring factors

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

Use live MarketData candles:

```powershell
$env:MARKETDATA_API_KEY="YOUR_MARKETDATA_TOKEN"
Invoke-RestMethod "http://127.0.0.1:8000/scan?symbols=NVDA,MSFT,AAPL,AVGO"
```

## MarketData credit controls

Live MarketData responses are cached on disk under `app/.cache/marketdata` by default. Historical dated bulk candles are cached without expiry; latest candle, option, news, and earnings responses use short TTLs so repeated dashboard scans do not repull the same provider data.

Useful settings:

```powershell
$env:MARKETDATA_CACHE_DIR="C:\path\to\marketdata-cache"
$env:MARKETDATA_LATEST_CACHE_SECONDS="300"
$env:MARKETDATA_CANDLE_CACHE_SECONDS="900"
$env:MARKETDATA_REFERENCE_CACHE_SECONDS="3600"
$env:MARKETDATA_PROVIDER_CACHE="true"
$env:MARKETDATA_PROVIDER_MAXAGE="5min"
$env:MARKETDATA_OPTION_STRIKE_LIMIT="5"
$env:MARKETDATA_OPTION_DTE="14"
$env:DISCOVERY_OPTIONS="true"
```

`MARKETDATA_PROVIDER_CACHE=true` adds MarketData `mode=cached&maxage=5min` to option-chain requests when supported by the plan. Candle and bulk-candle endpoints use the app's local disk cache because MarketData cached mode is not available for those endpoints.
`DISCOVERY_OPTIONS=true` enables put/call ratio, call volume, and preferred-contract enrichment in the discovery table.

## API

- `GET /health` returns service and API-key configuration status
- `GET /universe` returns the configured default universe
- `GET /scan?symbols=NVDA,MSFT&benchmark=QQQ&demo=true` ranks symbols
- `GET /phase2/candidates` returns precomputed PostgreSQL-backed momentum candidates
- `GET /phase2/status` returns the latest Phase 2 refresh job status
- `POST /scan` accepts JSON:

```json
{
  "symbols": ["NVDA", "MSFT", "AAPL"],
  "benchmark": "QQQ",
  "countback": 260,
  "demo": true
}
```

## Phase 2 precomputed screener

Phase 2 moves broad market discovery out of the click path:

```text
MarketData daily candles -> PostgreSQL daily_bars -> momentum_scores -> top candidates -> FastAPI reads
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
$env:PHASE2_SYMBOL_SOURCE="configured"
$env:PHASE2_SYMBOLS="NVDA,MSFT,AAPL,AVGO,AMD,META,GOOGL,AMZN,TSLA,SNDK,MU,WDC"
$env:PHASE2_SYMBOL_LIMIT="200"
$env:PHASE2_MIN_AVERAGE_VOLUME="3000000"
$env:PHASE2_VOLUME_PREFILTER_DAYS="20"
$env:PHASE2_MARKETDATA_CONCURRENCY="2"
..\.venv\Scripts\python.exe phase2_refresh.py --top 50
```

For broader scheduled runs, use `PHASE2_SYMBOL_SOURCE=listed`. The refresh prefilters that broad list by 20-day average daily volume over `3,000,000` before fetching full historical candles, but keep `PHASE2_SYMBOL_LIMIT` conservative until you understand MarketData credit usage. Options and LLM enrichment should remain downstream-only for the top candidates.

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
$env:MARKETDATA_API_KEY="YOUR_MARKETDATA_TOKEN"
$env:OPENAI_API_KEY="YOUR_OPENAI_API_KEY"
./deploy.ps1
```

Bash/Linux:

```bash
az account show
export MARKETDATA_API_KEY='YOUR_MARKETDATA_TOKEN'
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
