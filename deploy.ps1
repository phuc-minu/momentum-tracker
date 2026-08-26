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
$environmentId = $deploy.containerAppsEnvironmentId.value
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

if ($env:FMP_API_KEY) {
  az keyvault secret set --vault-name $vault --name fmp-api-key --value $env:FMP_API_KEY --output none
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
$envArgs += "DISCOVERY_OPTIONS=$discoveryOptions"

function Add-SecretEnvIfPresent {
  param(
    [string]$SecretName,
    [string]$EnvName
  )

  $secretId = az keyvault secret show --vault-name $vault --name $SecretName --query id -o tsv 2>$null
  if ($secretId) {
    $script:secretArgs += "$SecretName=keyvaultref:$secretId,identityref:$identityId"
    $script:envArgs += "$EnvName=secretref:$SecretName"
  }
}

Add-SecretEnvIfPresent "fmp-api-key" "FMP_API_KEY"
Add-SecretEnvIfPresent "marketdata-api-key" "MARKETDATA_OPTIONS_API_KEY"
Add-SecretEnvIfPresent "openai-api-key" "OPENAI_API_KEY"
Add-SecretEnvIfPresent "app-password" "APP_PASSWORD"
Add-SecretEnvIfPresent "app-session-secret" "APP_SESSION_SECRET"
Add-SecretEnvIfPresent "database-url" "DATABASE_URL"

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

$databaseSecretId = az keyvault secret show --vault-name $vault --name database-url --query id -o tsv 2>$null
$fmpSecretId = az keyvault secret show --vault-name $vault --name fmp-api-key --query id -o tsv 2>$null
if ($databaseSecretId -and $fmpSecretId) {
  $marketDataSecretId = az keyvault secret show --vault-name $vault --name marketdata-api-key --query id -o tsv 2>$null
  $includeMarketDataOptionsSecret = [bool]$marketDataSecretId
  az deployment group create `
    --resource-group $ResourceGroup `
    --template-file ./infra/db-refresh-job.bicep `
    --parameters location=$Location `
      jobName="$Prefix-db-refresh" `
      environmentId=$environmentId `
      image="$acrLogin/tech-momentum-ai:$imageTag" `
      registryServer=$acrLogin `
      identityId=$identityId `
      keyVaultName=$vault `
      includeMarketDataOptionsSecret=$includeMarketDataOptionsSecret `
    --output none
  Write-Host "Scheduled db refresh: weekdays at 22:30 UTC"
} else {
  Write-Warning "Skipping scheduled db refresh because database-url or fmp-api-key is missing from Key Vault."
}

$fqdn = az containerapp show --name $app --resource-group $ResourceGroup --query properties.configuration.ingress.fqdn -o tsv
Write-Host "Deployed: https://$fqdn"
Write-Host "Health:   https://$fqdn/health"
