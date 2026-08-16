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
$env:DISCOVERY_OPTIONS="true"
```

`MARKETDATA_PROVIDER_CACHE=true` adds MarketData `mode=cached&maxage=5min` to option-chain requests when supported by the plan. Candle and bulk-candle endpoints use the app's local disk cache because MarketData cached mode is not available for those endpoints.
`DISCOVERY_OPTIONS=true` enables put/call ratio, call volume, and preferred-contract enrichment in the discovery table.

## API

- `GET /health` returns service and API-key configuration status
- `GET /universe` returns the configured default universe
- `GET /scan?symbols=NVDA,MSFT&benchmark=QQQ&demo=true` ranks symbols
- `POST /scan` accepts JSON:

```json
{
  "symbols": ["NVDA", "MSFT", "AAPL"],
  "benchmark": "QQQ",
  "countback": 260,
  "demo": true
}
```

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
