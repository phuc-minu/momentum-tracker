#!/usr/bin/env bash
set -euo pipefail
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export AZURE_CORE_NO_COLOR=true

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-tech-momentum-ai}"
LOCATION="${LOCATION:-westus2}"
PREFIX="${PREFIX:-momentumai}"

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
image_tag="$(date +%Y%m%d%H%M%S)"

if [[ -n "${MARKETDATA_API_KEY:-}" ]]; then
  az keyvault secret set --vault-name "$vault" --name marketdata-api-key --value "$MARKETDATA_API_KEY" >/dev/null
fi

if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  az keyvault secret set --vault-name "$vault" --name openai-api-key --value "$OPENAI_API_KEY" >/dev/null
fi

az acr build --registry "$acr" --image "tech-momentum-ai:$image_tag" --no-logs ./app
acr_login=$(az acr show -n "$acr" --query loginServer -o tsv)
az containerapp registry set -n "$app" -g "$RESOURCE_GROUP" --server "$acr_login" --identity "$identity_id" >/dev/null

secret_args=()
env_args=()
if [[ -n "${MARKETDATA_API_KEY:-}" ]]; then
  secret_args+=("marketdata-api-key=keyvaultref:https://$vault.vault.azure.net/secrets/marketdata-api-key,identityref:$identity_id")
  env_args+=("MARKETDATA_API_KEY=secretref:marketdata-api-key")
fi
if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  secret_args+=("openai-api-key=keyvaultref:https://$vault.vault.azure.net/secrets/openai-api-key,identityref:$identity_id")
  env_args+=("OPENAI_API_KEY=secretref:openai-api-key")
fi

if [[ ${#secret_args[@]} -gt 0 ]]; then
  az containerapp secret set -n "$app" -g "$RESOURCE_GROUP" --secrets "${secret_args[@]}" >/dev/null
fi

if [[ ${#env_args[@]} -gt 0 ]]; then
  az containerapp update -n "$app" -g "$RESOURCE_GROUP" \
    --image "$acr_login/tech-momentum-ai:$image_tag" \
    --set-env-vars "${env_args[@]}" >/dev/null
else
  az containerapp update -n "$app" -g "$RESOURCE_GROUP" \
    --image "$acr_login/tech-momentum-ai:$image_tag" >/dev/null
fi

fqdn=$(az containerapp show -n "$app" -g "$RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)
echo "Deployed: https://$fqdn"
echo "Health:   https://$fqdn/health"
