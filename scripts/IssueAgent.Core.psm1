Set-StrictMode -Version Latest

function Get-LatestLabelEvent {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][object[]]$Events,
    [Parameter(Mandatory = $true)][string]$Label
  )

  $matching = @(
    $Events |
      Where-Object { $_.event -eq 'labeled' -and $_.label.name -eq $Label } |
      Sort-Object {
        [datetimeoffset]::Parse(
          [string]$_.created_at,
          [System.Globalization.CultureInfo]::InvariantCulture
        )
      } -Descending
  )
  if ($matching.Count -eq 0) { return $null }

  return $matching[0]
}

function Test-IssueEditedAfterLabel {
  [CmdletBinding()]
  param(
    [AllowEmptyString()][string]$LastEditedAt,
    [Parameter(Mandatory = $true)][datetime]$LabelTime
  )

  if ([string]::IsNullOrWhiteSpace($LastEditedAt)) { return $false }
  $editedAt = [datetimeoffset]::Parse(
    $LastEditedAt,
    [System.Globalization.CultureInfo]::InvariantCulture
  )
  return $editedAt.ToUniversalTime() -gt ([datetimeoffset]$LabelTime).ToUniversalTime()
}

function Get-RetryDelaySeconds {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][ValidateRange(1, 20)][int]$Attempt,
    [int]$BaseDelaySeconds = 300,
    [int]$MaximumDelaySeconds = 3600
  )

  $delay = [double]$BaseDelaySeconds * [math]::Pow(2, $Attempt - 1)
  return [int][math]::Min($MaximumDelaySeconds, $delay)
}

function Test-IssueAgentRetryDue {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][long]$RetryAtUnix,
    [long]$NowUnix = [datetimeoffset]::UtcNow.ToUnixTimeSeconds()
  )

  return $RetryAtUnix -le $NowUnix
}

function Get-ReviewVerdict {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string]$Review)

  $match = [regex]::Match($Review, '(?im)^\s*\*{0,2}\s*VERDICT\s*:\s*(PASS|BLOCKED)\b')
  if (-not $match.Success) { return 'INVALID' }
  return $match.Groups[1].Value.ToUpperInvariant()
}

function Get-DeploymentCommitDisposition {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$CandidateCommit,
    [AllowEmptyString()][string]$CurrentlyDeployedCommit,
    [bool]$CandidateIsAncestorOfDeployed = $false,
    [bool]$DeployedIsAncestorOfCandidate = $false
  )

  if ([string]::IsNullOrWhiteSpace($CurrentlyDeployedCommit)) { return 'deploy' }
  if ($CandidateCommit -eq $CurrentlyDeployedCommit -or $CandidateIsAncestorOfDeployed) {
    return 'already-deployed'
  }
  if ($DeployedIsAncestorOfCandidate) { return 'deploy' }
  return 'reject'
}

function Read-IssueAgentState {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) { return @{} }
  return Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
}

function Save-IssueAgentState {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][hashtable]$State
  )

  $directory = Split-Path -Parent $Path
  New-Item -ItemType Directory -Force -Path $directory | Out-Null
  $temporaryFile = Join-Path $directory ("state.{0}.tmp" -f [guid]::NewGuid())
  try {
    $json = ConvertTo-Json -InputObject $State -Depth 16
    [System.IO.File]::WriteAllText($temporaryFile, $json, [System.Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $Path) {
      [System.IO.File]::Move($temporaryFile, $Path, $true)
    }
    else {
      Move-Item -LiteralPath $temporaryFile -Destination $Path
    }
  }
  finally {
    Remove-Item -LiteralPath $temporaryFile -Force -ErrorAction SilentlyContinue
  }
}

function Test-PullRequestDeploymentEligible {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][hashtable]$IssueState,
    [Parameter(Mandatory = $true)][object]$PullRequest,
    [Parameter(Mandatory = $true)][string]$DefaultBranch
  )

  $expectedHeadBranch = if ($IssueState.ContainsKey('headBranch') -and
      -not [string]::IsNullOrWhiteSpace([string]$IssueState.headBranch)) {
    [string]$IssueState.headBranch
  } else {
    "agent/issue-$($IssueState.issueNumber)"
  }
  if ($PullRequest -is [System.Collections.IDictionary]) {
    if (-not $PullRequest.Contains('isCrossRepository')) { return $false }
    $isCrossRepository = [bool]$PullRequest['isCrossRepository']
  }
  else {
    $crossRepositoryProperty = $PullRequest.PSObject.Properties['isCrossRepository']
    if ($null -eq $crossRepositoryProperty) { return $false }
    $isCrossRepository = [bool]$crossRepositoryProperty.Value
  }
  if ($IssueState.stage -notin @('awaiting-human-review', 'deploying') -or
      [int]$IssueState.prNumber -le 0 -or
      [string]$PullRequest.state -ne 'MERGED' -or
      [string]$PullRequest.baseRefName -ne $DefaultBranch -or
      [string]$PullRequest.headRefName -ne $expectedHeadBranch -or
      $isCrossRepository -or
      [string]::IsNullOrWhiteSpace([string]$PullRequest.mergeCommit.oid)) {
    return $false
  }

  return [string]$PullRequest.mergeCommit.oid -match '^[0-9a-fA-F]{40}$'
}

Export-ModuleMember -Function Get-LatestLabelEvent, Test-IssueEditedAfterLabel, Get-RetryDelaySeconds, Test-IssueAgentRetryDue, Get-ReviewVerdict, Get-DeploymentCommitDisposition, Read-IssueAgentState, Save-IssueAgentState, Test-PullRequestDeploymentEligible
