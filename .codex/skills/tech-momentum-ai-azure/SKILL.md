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
- Container App FQDN: `momentumai-api.bluepebble-c6bb0fe0.westus2.azurecontainerapps.io`
- Public app URL: `https://momentumai-api.bluepebble-c6bb0fe0.westus2.azurecontainerapps.io`
- Health URL: `https://momentumai-api.bluepebble-c6bb0fe0.westus2.azurecontainerapps.io/health`
- Azure Container Registry: `momentumairkdchlu5eqdos`
- ACR login server: `momentumairkdchlu5eqdos.azurecr.io`
- Key Vault: `momentumai-rkdchlu5eqdos`
- User-assigned identity: `momentumai-identity`
- Container Apps environment: `momentumai-env`
- Log Analytics workspace: `momentumai-logs`

## Secrets

Key Vault stores:
- `openai-api-key`
- `marketdata-api-key`

Container App secret refs use the same secret names and expose:
- `OPENAI_API_KEY=secretref:openai-api-key`
- `MARKETDATA_API_KEY=secretref:marketdata-api-key`

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
- Writes `MARKETDATA_API_KEY` and `OPENAI_API_KEY` from local environment variables into Key Vault when set.
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

Current known result: `16 passed`.

## Operational Notes

- Azure CLI may require sandbox escalation because it reads `C:\Users\phuct\.azure\azureProfile.json`.
- Git may warn about denied access to `C:\Users\phuct\.config\git\ignore`; this warning has not blocked commits or pushes.
- `gh` CLI was not installed as of August 16, 2026. Use normal `git` for commit/push unless GitHub PR automation is specifically needed.
- MarketData live calls can hit account credit limits. On August 16, 2026, a rate-limit reset header of `1786973400` decoded to `2026-08-17T13:30:00+00:00`.
- The frontend now falls back to beta/demo data when live MarketData scanner or discovery requests fail.
- The dashboard and token-spending endpoints require login. `/health` and static assets remain public.

## Recent Commits

- `6ecf66e` - `Update discovery table layout`
- `0eb0036` - `Fallback to beta data on live MarketData errors`
