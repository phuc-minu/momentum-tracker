param(
  [string]$ResourceGroup = "rg-tech-momentum-ai",
  [string]$Location = "westus2",
  [string]$Prefix = "momentumai"
)

$ErrorActionPreference = "Stop"

if (-not $env:MARKETDATA_API_KEY) { throw "Set MARKETDATA_API_KEY in this terminal first." }
if (-not $env:OPENAI_API_KEY) { throw "Set OPENAI_API_KEY in this terminal first." }

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

az keyvault secret set --vault-name $vault --name marketdata-api-key --value $env:MARKETDATA_API_KEY --output none
az keyvault secret set --vault-name $vault --name openai-api-key --value $env:OPENAI_API_KEY --output none

az acr build --registry $acr --image tech-momentum-ai:latest ./app

$acrLogin = az acr show --name $acr --query loginServer -o tsv
az containerapp registry set --name $app --resource-group $ResourceGroup --server $acrLogin --identity $identityId --output none

$marketSecretUri = "https://$vault.vault.azure.net/secrets/marketdata-api-key"
$openaiSecretUri = "https://$vault.vault.azure.net/secrets/openai-api-key"
az containerapp secret set --name $app --resource-group $ResourceGroup --secrets `
  "marketdata-api-key=keyvaultref:$marketSecretUri,identityref:$identityId" `
  "openai-api-key=keyvaultref:$openaiSecretUri,identityref:$identityId" --output none

Write-Host "Waiting for container app identity access to Key Vault..."
Start-Sleep -Seconds 30

az containerapp update --name $app --resource-group $ResourceGroup `
  --image "$acrLogin/tech-momentum-ai:latest" `
  --set-env-vars "MARKETDATA_API_KEY=secretref:marketdata-api-key" "OPENAI_API_KEY=secretref:openai-api-key" --output none

$fqdn = az containerapp show --name $app --resource-group $ResourceGroup --query properties.configuration.ingress.fqdn -o tsv
Write-Host "Deployed: https://$fqdn"
Write-Host "Health:   https://$fqdn/health"
