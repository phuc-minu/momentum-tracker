targetScope = 'resourceGroup'

@description('Azure region for the scheduled refresh job.')
param location string = resourceGroup().location

@description('Container Apps Job resource name.')
param jobName string

@description('Resource ID of the existing Container Apps environment.')
param environmentId string

@description('Container image containing phase2_refresh.py.')
param image string

@description('Azure Container Registry login server.')
param registryServer string

@description('User-assigned managed identity resource ID.')
param identityId string

@description('Key Vault containing the database and provider secrets.')
param keyVaultName string

@description('Include MarketData options enrichment when its Key Vault secret exists.')
param includeMarketDataSecret bool = false

var databaseSecret = {
  name: 'database-url'
  keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/database-url'
  identity: identityId
}
var fmpSecret = {
  name: 'fmp-api-key'
  keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/fmp-api-key'
  identity: identityId
}
var marketDataSecrets = includeMarketDataSecret ? [
  {
    name: 'marketdata-api-key'
    keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/marketdata-api-key'
    identity: identityId
  }
] : []
var marketDataEnvironment = includeMarketDataSecret ? [
  {
    name: 'MARKETDATA_API_KEY'
    secretRef: 'marketdata-api-key'
  }
] : []

resource refreshJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1800
      replicaRetryLimit: 1
      scheduleTriggerConfig: {
        cronExpression: '30 22 * * 1-5'
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registryServer
          identity: identityId
        }
      ]
      secrets: concat([
        databaseSecret
        fmpSecret
      ], marketDataSecrets)
    }
    template: {
      containers: [
        {
          name: 'db-refresh'
          image: image
          command: [
            'python'
          ]
          args: [
            'phase2_refresh.py'
          ]
          env: concat([
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'FMP_API_KEY'
              secretRef: 'fmp-api-key'
            }
            {
              name: 'PHASE2_SYMBOL_SOURCE'
              value: 'fmp'
            }
            {
              name: 'PHASE2_SYMBOL_LIMIT'
              value: '500'
            }
            {
              name: 'PHASE2_TOP_N'
              value: '50'
            }
            {
              name: 'PHASE2_COUNTBACK'
              value: '260'
            }
            {
              name: 'PHASE2_MIN_AVERAGE_VOLUME'
              value: '3000000'
            }
            {
              name: 'PHASE2_MIN_PRICE'
              value: '10'
            }
            {
              name: 'PHASE2_FMP_CONCURRENCY'
              value: '2'
            }
            {
              name: 'PHASE2_OPTIONS_CONCURRENCY'
              value: '2'
            }
            {
              name: 'FMP_POSITIONS_SUMMARY_ENABLED'
              value: 'false'
            }
            {
              name: 'MARKETDATA_PROVIDER_CACHE'
              value: 'false'
            }
            {
              name: 'MARKETDATA_OPTION_STRIKE_LIMIT'
              value: '5'
            }
            {
              name: 'MARKETDATA_OPTION_DTE'
              value: '14'
            }
          ], marketDataEnvironment)
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

output jobName string = refreshJob.name
output cronExpression string = refreshJob.properties.configuration.scheduleTriggerConfig.cronExpression
