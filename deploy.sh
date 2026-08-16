#!/usr/bin/env bash
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-tech-momentum-ai}"
LOCATION="${LOCATION:-westus2}"
PREFIX="${PREFIX:-momentumai}"

: "${MARKETDATA_API_KEY:?Set MARKETDATA_API_KEY first}"
: "${OPENAI_API_KEY:?Set OPENAI_API_KEY first}"

az account show >/dev/null
for p in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry Microsoft.KeyVault; do
  az provider register --namespace "$p" --wait >/dev/null
done

az group create -n "$RESOURCE_GROUP" -l "$LOCATION" >/dev/null
outputs=$(az deployment group create -g "$RESOURCE_GROUP" -f ./infra/main.bicep -p location="$LOCATION" prefix="$PREFIX" --query properties.outputs -o json)

acr=$(echo "$outputs" | python -c 'import json,sys; print(json.load(sys.stdin)["acrName"]["value"])')
vault=$(echo "$outputs" | python -c 'import json,sys; print(json.load(sys.stdin)["keyVaultName"]["value"])')
identity_id=$(echo "$outputs" | python -c 'import json,sys; print(json.load(sys.stdin)["identityId"]["value"])')
app=$(echo "$outputs" | python -c 'import json,sys; print(json.load(sys.stdin)["containerAppName"]["value"])')

az keyvault secret set --vault-name "$vault" --name marketdata-api-key --value "$MARKETDATA_API_KEY" >/dev/null
az keyvault secret set --vault-name "$vault" --name openai-api-key --value "$OPENAI_API_KEY" >/dev/null

az acr build --registry "$acr" --image tech-momentum-ai:latest ./app
acr_login=$(az acr show -n "$acr" --query loginServer -o tsv)
az containerapp registry set -n "$app" -g "$RESOURCE_GROUP" --server "$acr_login" --identity "$identity_id" >/dev/null

az containerapp secret set -n "$app" -g "$RESOURCE_GROUP" --secrets \
  "marketdata-api-key=keyvaultref:https://$vault.vault.azure.net/secrets/marketdata-api-key,identityref:$identity_id" \
  "openai-api-key=keyvaultref:https://$vault.vault.azure.net/secrets/openai-api-key,identityref:$identity_id" >/dev/null

az containerapp update -n "$app" -g "$RESOURCE_GROUP" \
  --image "$acr_login/tech-momentum-ai:latest" \
  --set-env-vars MARKETDATA_API_KEY=secretref:marketdata-api-key OPENAI_API_KEY=secretref:openai-api-key >/dev/null

fqdn=$(az containerapp show -n "$app" -g "$RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)
echo "Deployed: https://$fqdn"
echo "Health:   https://$fqdn/health"
