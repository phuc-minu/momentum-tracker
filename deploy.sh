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

if [[ -n "${FMP_API_KEY:-}" ]]; then
  az keyvault secret set --vault-name "$vault" --name fmp-api-key --value "$FMP_API_KEY" >/dev/null
fi

if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  az keyvault secret set --vault-name "$vault" --name openai-api-key --value "$OPENAI_API_KEY" >/dev/null
fi

if [[ -n "${APP_PASSWORD:-}" ]]; then
  az keyvault secret set --vault-name "$vault" --name app-password --value "$APP_PASSWORD" >/dev/null
fi

if [[ -n "${APP_SESSION_SECRET:-}" ]]; then
  az keyvault secret set --vault-name "$vault" --name app-session-secret --value "$APP_SESSION_SECRET" >/dev/null
fi

if [[ -n "${DATABASE_URL:-}" ]]; then
  az keyvault secret set --vault-name "$vault" --name database-url --value "$DATABASE_URL" >/dev/null
fi

az acr build --registry "$acr" --image "tech-momentum-ai:$image_tag" --no-logs ./app
acr_login=$(az acr show -n "$acr" --query loginServer -o tsv)
az containerapp registry set -n "$app" -g "$RESOURCE_GROUP" --server "$acr_login" --identity "$identity_id" >/dev/null

secret_args=()
env_args=()

add_secret_env_if_present() {
  local secret_name="$1"
  local env_name="$2"
  local secret_id
  secret_id="$(az keyvault secret show --vault-name "$vault" --name "$secret_name" --query id -o tsv 2>/dev/null || true)"
  if [[ -n "$secret_id" ]]; then
    secret_args+=("$secret_name=keyvaultref:$secret_id,identityref:$identity_id")
    env_args+=("$env_name=secretref:$secret_name")
  fi
}

add_secret_env_if_present "fmp-api-key" "FMP_API_KEY"
add_secret_env_if_present "openai-api-key" "OPENAI_API_KEY"
add_secret_env_if_present "app-password" "APP_PASSWORD"
add_secret_env_if_present "app-session-secret" "APP_SESSION_SECRET"
add_secret_env_if_present "database-url" "DATABASE_URL"

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
