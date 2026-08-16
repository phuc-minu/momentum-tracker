# Tech Momentum AI — Azure MVP

This is the Azure deployment scaffold for a momentum-first investing agent.

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

## Next implementation step

Add the MarketData historical candle client, universe loader, momentum score, QQQ-relative-strength score, breakout/pullback classification, and GPT catalyst analysis.
