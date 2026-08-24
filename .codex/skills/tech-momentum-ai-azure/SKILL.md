---
name: tech-momentum-ai-azure
description: Project runbook for the Tech Momentum AI Azure deployment. Use when Codex needs to deploy, verify, inspect, troubleshoot, update secrets, or remember Azure resource names for this repository after a restarted session.
---

# Tech Momentum AI Azure

## Overview

Use this skill for this repository:
`C:\Users\phuct\Downloads\tech-momentum-ai-azure-mvp\tech-momentum-ai`

This skill stores non-secret deployment memory for the Stock Momentum AI / Tech Momentum AI Azure MVP. Never store actual API key values here.

## Azure Resources

- Subscription name: `phuc-alpha`
- Subscription id: `0d9b5f21-e2d3-492b-9dac-ad25d81e7367`
- Tenant id: `f114a6cf-99f0-42a5-beeb-2318ee5f563a`
- Resource group: `rg-tech-momentum-ai`
- Region: `westus2`
- Prefix: `momentumai`
- Container App: `momentumai-api`
- Container Apps Job: `momentumai-db-refresh`
- DB refresh schedule: `30 22 * * 1-5` (22:30 UTC Monday-Friday)
- Container App FQDN: `momentumai-api.bluepebble-c6bb0fe0.westus2.azurecontainerapps.io`
- Public app URL: `https://momentumai-api.bluepebble-c6bb0fe0.westus2.azurecontainerapps.io`
- Health URL: `https://momentumai-api.bluepebble-c6bb0fe0.westus2.azurecontainerapps.io/health`
- Azure Container Registry: `momentumairkdchlu5eqdos`
- ACR login server: `momentumairkdchlu5eqdos.azurecr.io`
- Key Vault: `momentumai-rkdchlu5eqdos`
- User-assigned identity: `momentumai-identity`
- Container Apps environment: `momentumai-env`
- Log Analytics workspace: `momentumai-logs`
- PostgreSQL Flexible Server: `momentumai-pg-rkdchlu5eqdos`
- PostgreSQL FQDN: `momentumai-pg-rkdchlu5eqdos.postgres.database.azure.com`
- PostgreSQL database: `momentumai`
- PostgreSQL admin user: `momentumadmin`
- PostgreSQL SKU: `Standard_B1ms` / Burstable, 32 GiB storage

## Secrets

Key Vault stores:
- `openai-api-key`
- `fmp-api-key`
- `database-url`

Container App secret refs use the same secret names and expose:
- `OPENAI_API_KEY=secretref:openai-api-key`
- `FMP_API_KEY=secretref:fmp-api-key`
- `DATABASE_URL=secretref:database-url` when Phase 2 PostgreSQL is configured

Do not print secret values. Check only presence or configuration status.

Authentication settings:
- `APP_PASSWORD` is required for dashboard login and should be stored as a Key Vault-backed Container App secret.
- `APP_SESSION_SECRET` optionally controls the HMAC cookie signing secret.
- If `APP_SESSION_SECRET` is not configured, the app derives the cookie token from `APP_PASSWORD`.

## Deploy

From the repository root, deploy with:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy.ps1
```

The normal script does all of the following:
- Ensures Azure providers and resource group exist.
- Deploys `infra/main.bicep`.
- Writes `FMP_API_KEY` and `OPENAI_API_KEY` from local environment variables into Key Vault when set.
- Builds the app with ACR as `tech-momentum-ai:<timestamp>`.
- Updates `momentumai-api` to the new image and secret references.

If PowerShell blocks `.\deploy.ps1` because it is not signed, use the process-scoped bypass command above.

## Verify

Check the deployed image and revision:

```powershell
az containerapp show --name momentumai-api --resource-group rg-tech-momentum-ai --query "{revision:properties.latestRevisionName,image:properties.template.containers[0].image,fqdn:properties.configuration.ingress.fqdn}" -o json
```

Check health:

```powershell
.\.venv\Scripts\python.exe -c "import urllib.request; print(urllib.request.urlopen('https://momentumai-api.bluepebble-c6bb0fe0.westus2.azurecontainerapps.io/health', timeout=30).read().decode())"
```

Prefer the Python health check if `Invoke-RestMethod` or `curl.exe` fails locally with Windows Schannel credential errors.

Expected healthy response:

```json
{"status":"ok","marketdata_configured":true,"openai_configured":true}
```

## Tests

Run tests from `app` using the project virtualenv:

```powershell
..\.venv\Scripts\python.exe -m pytest
```

Current known result: `21 passed`.

## Phase 2 Pipeline

Job alias:
- `db refresh` means run `app/phase2_refresh.py` to refresh Azure PostgreSQL Phase 2 data.
- Azure schedules `db refresh` through the `momentumai-db-refresh` Container Apps Job at 22:30 UTC Monday-Friday.

Phase 2 adds a PostgreSQL-backed precomputed screener:
- `app/phase2_db.py` defines PostgreSQL tables for `symbols`, `daily_bars`, `momentum_scores`, `option_scores`, and `refresh_runs`.
- `app/phase2_refresh.py` is the scheduled/manual Python refresh job.
- `app/phase2_pipeline.py` converts candles to stored bars and ranks momentum candidates.
- `app/symbol_source.py` loads symbols from `PHASE2_SYMBOLS` or the Nasdaq Trader symbol directory.
- FastAPI exposes authenticated read endpoints: `/phase2/candidates` and `/phase2/status`.

Safe refresh knobs:
- `PHASE2_SYMBOL_SOURCE=fmp|configured|listed|nasdaq`
- `DISCOVERY_SYMBOL_SOURCE=fmp|static`
- `DISCOVERY_SYMBOL_LIMIT=500`
- `DISCOVERY_MIN_AVERAGE_VOLUME=3000000`
- `PHASE2_SYMBOLS=NVDA,MSFT,...`
- `PHASE2_SYMBOL_LIMIT=500`
- `PHASE2_COUNTBACK=60`
- `PHASE2_TOP_N=50`
- `PHASE2_MIN_PRICE=10`
- `PHASE2_FMP_CONCURRENCY=2`

Run the demo job from `app`:

```powershell
..\.venv\Scripts\python.exe phase2_refresh.py --demo --top 50
```

Local PostgreSQL:
- PostgreSQL 17 is installed locally.
- Service: `postgresql-x64-17`, automatic startup.
- `psql.exe`: `C:\Program Files\PostgreSQL\17\bin\psql.exe`

Azure PostgreSQL access:
- `DATABASE_URL` is stored in Key Vault as `database-url` and exposed to the Container App as `DATABASE_URL`.
- Firewall has an Azure-services rule (`0.0.0.0`) for Container App access.
- Firewall has a `local-dev` rule for the current local public IP used during setup.
- Local encrypted admin-password helper is under `.local/` and `.local/` is gitignored. Do not commit it.
- Latest verified demo refresh wrote Phase 2 candidates successfully; `/phase2/status` reported `succeeded`.

## Operational Notes

- Azure CLI may require sandbox escalation because it reads `C:\Users\phuct\.azure\azureProfile.json`.
- Git may warn about denied access to `C:\Users\phuct\.config\git\ignore`; this warning has not blocked commits or pushes.
- `gh` CLI was not installed as of August 16, 2026. Use normal `git` for commit/push unless GitHub PR automation is specifically needed.
- FMP is the only live market data provider. Use `FMP_API_KEY` with base URL `https://financialmodelingprep.com/stable/`. Live Discovery and Phase 2 refresh default to FMP `company-screener` for symbol selection.
- The frontend now falls back to beta/demo data when live FMP scanner or discovery requests fail.
- The dashboard and token-spending endpoints require login. `/health` and static assets remain public.
- Phase 2 deliberately keeps broad market scans out of the user click path. Use scheduled refreshes and conservative symbol limits to control FMP usage.
- Momentum scoring is 30-calendar-day focused. It no longer requires 200D SMA / one-year history; scheduled refreshes use `PHASE2_COUNTBACK=60` so 50D SMA remains available when possible.

## Recent Commits

- `6ecf66e` - `Update discovery table layout`
- `0eb0036` - `Fallback to beta data on live MarketData errors`
