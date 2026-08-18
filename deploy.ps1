param(
  [string]$ResourceGroup = "rg-tech-momentum-ai",
  [string]$Location = "westus2",
  [string]$Prefix = "momentumai"
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:AZURE_CORE_NO_COLOR = "true"

az account show --output none
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az provider register --namespace Microsoft.ContainerRegistry --wait
az provider register --namespace Microsoft.KeyVault --wait

az group create --name $ResourceGroup --location $Location --output none

$deploy = az deployment group create `
  --resource-group $ResourceGroup `
  --template-file ./infra/main.bicep `
  --parameters location=$Location prefix=$Prefix `
  --query properties.outputs -o json | ConvertFrom-Json

$acr = $deploy.acrName.value
$vault = $deploy.keyVaultName.value
$identityId = $deploy.identityId.value
$app = $deploy.containerAppName.value
$subscriptionId = az account show --query id -o tsv
$vaultId = "/subscriptions/$subscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.KeyVault/vaults/$vault"
$imageTag = Get-Date -Format "yyyyMMddHHmmss"

Write-Host "Granting current Azure user permission to write Key Vault secrets..."
$signedInUserId = az ad signed-in-user show --query id -o tsv
az role assignment create `
  --assignee-object-id $signedInUserId `
  --assignee-principal-type User `
  --role "Key Vault Secrets Officer" `
  --scope $vaultId `
  --output none

Write-Host "Waiting for Key Vault RBAC propagation..."
Start-Sleep -Seconds 30

if ($env:MARKETDATA_API_KEY) {
  az keyvault secret set --vault-name $vault --name marketdata-api-key --value $env:MARKETDATA_API_KEY --output none
}

if ($env:OPENAI_API_KEY) {
  az keyvault secret set --vault-name $vault --name openai-api-key --value $env:OPENAI_API_KEY --output none
}

if ($env:APP_PASSWORD) {
  az keyvault secret set --vault-name $vault --name app-password --value $env:APP_PASSWORD --output none
}

if ($env:APP_SESSION_SECRET) {
  az keyvault secret set --vault-name $vault --name app-session-secret --value $env:APP_SESSION_SECRET --output none
}

if ($env:DATABASE_URL) {
  az keyvault secret set --vault-name $vault --name database-url --value $env:DATABASE_URL --output none
}

az acr build --registry $acr --image "tech-momentum-ai:$imageTag" --no-logs ./app

$acrLogin = az acr show --name $acr --query loginServer -o tsv
az containerapp registry set --name $app --resource-group $ResourceGroup --server $acrLogin --identity $identityId --output none

$secretArgs = @()
$envArgs = @()
$discoveryOptions = if ($env:DISCOVERY_OPTIONS) { $env:DISCOVERY_OPTIONS } else { "true" }
$marketDataProviderCache = if ($env:MARKETDATA_PROVIDER_CACHE) { $env:MARKETDATA_PROVIDER_CACHE } else { "false" }
$marketDataOptionStrikeLimit = if ($env:MARKETDATA_OPTION_STRIKE_LIMIT) { $env:MARKETDATA_OPTION_STRIKE_LIMIT } else { "5" }
$marketDataOptionDte = if ($env:MARKETDATA_OPTION_DTE) { $env:MARKETDATA_OPTION_DTE } else { "14" }
$envArgs += "DISCOVERY_OPTIONS=$discoveryOptions"
$envArgs += "MARKETDATA_PROVIDER_CACHE=$marketDataProviderCache"
$envArgs += "MARKETDATA_OPTION_STRIKE_LIMIT=$marketDataOptionStrikeLimit"
$envArgs += "MARKETDATA_OPTION_DTE=$marketDataOptionDte"
if ($env:MARKETDATA_API_KEY) {
  $marketSecretUri = "https://$vault.vault.azure.net/secrets/marketdata-api-key"
  $secretArgs += "marketdata-api-key=keyvaultref:$marketSecretUri,identityref:$identityId"
  $envArgs += "MARKETDATA_API_KEY=secretref:marketdata-api-key"
}
if ($env:OPENAI_API_KEY) {
  $openaiSecretUri = "https://$vault.vault.azure.net/secrets/openai-api-key"
  $secretArgs += "openai-api-key=keyvaultref:$openaiSecretUri,identityref:$identityId"
  $envArgs += "OPENAI_API_KEY=secretref:openai-api-key"
}
if ($env:APP_PASSWORD) {
  $appPasswordSecretUri = "https://$vault.vault.azure.net/secrets/app-password"
  $secretArgs += "app-password=keyvaultref:$appPasswordSecretUri,identityref:$identityId"
  $envArgs += "APP_PASSWORD=secretref:app-password"
}
if ($env:APP_SESSION_SECRET) {
  $appSessionSecretUri = "https://$vault.vault.azure.net/secrets/app-session-secret"
  $secretArgs += "app-session-secret=keyvaultref:$appSessionSecretUri,identityref:$identityId"
  $envArgs += "APP_SESSION_SECRET=secretref:app-session-secret"
}
if ($env:DATABASE_URL) {
  $databaseUrlSecretUri = "https://$vault.vault.azure.net/secrets/database-url"
  $secretArgs += "database-url=keyvaultref:$databaseUrlSecretUri,identityref:$identityId"
  $envArgs += "DATABASE_URL=secretref:database-url"
}

if ($secretArgs.Count -gt 0) {
  az containerapp secret set --name $app --resource-group $ResourceGroup --secrets $secretArgs --output none
  Write-Host "Waiting for container app identity access to Key Vault..."
  Start-Sleep -Seconds 30
}

if ($envArgs.Count -gt 0) {
  az containerapp update --name $app --resource-group $ResourceGroup `
    --image "$acrLogin/tech-momentum-ai:$imageTag" `
    --set-env-vars $envArgs --output none
} else {
  az containerapp update --name $app --resource-group $ResourceGroup `
    --image "$acrLogin/tech-momentum-ai:$imageTag" --output none
}

$fqdn = az containerapp show --name $app --resource-group $ResourceGroup --query properties.configuration.ingress.fqdn -o tsv
Write-Host "Deployed: https://$fqdn"
Write-Host "Health:   https://$fqdn/health"
