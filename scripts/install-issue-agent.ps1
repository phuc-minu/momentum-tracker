[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string]$Repository = 'phuc-minu/momentum-tracker',
  [string]$TaskName = 'TechMomentumAI-IssueAgent'
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
Set-StrictMode -Version Latest

$runtimeRoot = Join-Path $env:LOCALAPPDATA 'TechMomentumAI\issue-agent'
$repositoryPath = Join-Path $runtimeRoot 'repo'
$requiredClis = @('gh', 'claude', 'az', 'docker')
foreach ($cli in $requiredClis) {
  if (-not (Get-Command $cli -ErrorAction SilentlyContinue)) {
    throw "Required command '$cli' was not found on PATH."
  }
}
if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable('ISSUE_AGENT_CODEX_API_KEY', 'User'))) {
  throw 'Set ISSUE_AGENT_CODEX_API_KEY as a current-user environment variable using a dedicated AI-only API key before installing.'
}

& gh auth status
if ($LASTEXITCODE -ne 0) { throw 'GitHub CLI authentication is required before installing the issue agent.' }
& az account show --output none
if ($LASTEXITCODE -ne 0) { throw 'Azure CLI authentication is required before installing the issue agent.' }
$dockerOs = (& docker info --format '{{.OSType}}').Trim()
if ($LASTEXITCODE -ne 0 -or $dockerOs -ne 'linux') {
  throw 'A running Docker Engine in Linux container mode is required for the isolated Codex worker and test runner.'
}

$branch = (& gh repo view $Repository --json defaultBranchRef --jq '.defaultBranchRef.name').Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($branch)) {
  throw "Could not read the default branch for $Repository."
}

if (-not $PSCmdlet.ShouldProcess($repositoryPath, "Install the current-user issue agent task for $Repository")) {
  return
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask -and $existingTask.State -eq 'Running') {
  throw "Task '$TaskName' is currently running. Wait for it to finish before updating the agent installation."
}
if (Test-Path -LiteralPath (Join-Path $repositoryPath '.git')) {
  & git -C $repositoryPath fetch origin $branch
  if ($LASTEXITCODE -ne 0) { throw 'Could not fetch the agent repository updates.' }
  & git -C $repositoryPath pull --ff-only origin $branch
  if ($LASTEXITCODE -ne 0) { throw 'Could not fast-forward the local agent repository.' }
}
else {
  & gh repo clone $Repository $repositoryPath
  if ($LASTEXITCODE -ne 0) { throw "Could not clone $Repository into LocalAppData." }
}

$requirementsPath = Join-Path $repositoryPath 'app\requirements.txt'
if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
  throw "Python requirements file is missing: $requirementsPath"
}

$agentScript = Join-Path $repositoryPath 'scripts\issue-agent.ps1'
if (-not (Test-Path -LiteralPath $agentScript)) {
  throw 'The repository default branch does not contain scripts\issue-agent.ps1 yet.'
}

$action = New-ScheduledTaskAction `
  -Execute 'pwsh.exe' `
  -Argument "-NoLogo -NoProfile -WindowStyle Hidden -File `"$agentScript`" -Mode poll -Repository `"$Repository`" -RepoRoot `"$repositoryPath`"" `
  -WorkingDirectory $repositoryPath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
  -MultipleInstances IgnoreNew `
  -StartWhenAvailable `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 5) `
  -ExecutionTimeLimit ([timespan]::Zero)
$principal = New-ScheduledTaskPrincipal `
  -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
  -LogonType Interactive `
  -RunLevel Limited

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Description "Polls trusted ai-ready issues in $Repository and deploys only merged PR commits." `
  -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Output "Installed and started current-user task '$TaskName'. It also runs at sign-in and stores state/logs under $runtimeRoot."
