$modulePath = Join-Path (Split-Path -Parent $PSScriptRoot) 'IssueAgent.Core.psm1'
Import-Module $modulePath -Force
$pesterMajorVersion = (Get-Module Pester).Version.Major

function Assert-ShouldBe($Actual, $Expected) {
  if ($pesterMajorVersion -ge 5) {
    $Actual | Should -Be $Expected
  }
  else {
    $Actual | Should Be $Expected
  }
}

Describe 'Issue agent authorization and retry policy' {
  It 'uses the latest event that applied the ready label' {
    $events = @(
      @{ event = 'labeled'; created_at = '2025-01-01T00:00:00Z'; label = @{ name = 'ai-ready' }; actor = @{ login = 'old-user' } },
      @{ event = 'labeled'; created_at = '2025-01-02T00:00:00Z'; label = @{ name = 'ai-ready' }; actor = @{ login = 'trusted-user' } },
      @{ event = 'labeled'; created_at = '2025-01-03T00:00:00Z'; label = @{ name = 'triage' }; actor = @{ login = 'other-user' } }
    )

    $event = Get-LatestLabelEvent -Events $events -Label 'ai-ready'
    Assert-ShouldBe $event.actor.login 'trusted-user'
  }

  It 'detects issue edits made after trusted labeling' {
    Assert-ShouldBe (Test-IssueEditedAfterLabel -LastEditedAt '2025-01-02T00:00:00Z' -LabelTime ([datetime]'2025-01-01T00:00:00Z')) $true
    Assert-ShouldBe (Test-IssueEditedAfterLabel -LastEditedAt '2025-01-02T00:00:00Z' -LabelTime ([datetime]'2025-01-03T00:00:00Z')) $false
  }

  It 'bounds exponential retry delays' {
    Assert-ShouldBe (Get-RetryDelaySeconds -Attempt 1) 300
    Assert-ShouldBe (Get-RetryDelaySeconds -Attempt 3) 1200
    Assert-ShouldBe (Get-RetryDelaySeconds -Attempt 10) 3600
  }

  It 'uses UTC epoch timestamps for retry deadlines' {
    Assert-ShouldBe (Test-IssueAgentRetryDue -RetryAtUnix 100 -NowUnix 99) $false
    Assert-ShouldBe (Test-IssueAgentRetryDue -RetryAtUnix 100 -NowUnix 100) $true
  }

  It 'accepts bold review verdicts and rejects missing verdicts' {
    Assert-ShouldBe (Get-ReviewVerdict -Review '**VERDICT: PASS** — no blocking findings') 'PASS'
    Assert-ShouldBe (Get-ReviewVerdict -Review 'VERDICT: BLOCKED - fix required') 'BLOCKED'
    Assert-ShouldBe (Get-ReviewVerdict -Review 'Review looks good') 'INVALID'
  }

  It 'prevents deploying a commit older than or divergent from production' {
    Assert-ShouldBe (Get-DeploymentCommitDisposition -CandidateCommit 'a' -CurrentlyDeployedCommit '') 'deploy'
    Assert-ShouldBe (Get-DeploymentCommitDisposition -CandidateCommit 'a' -CurrentlyDeployedCommit 'a') 'already-deployed'
    Assert-ShouldBe (Get-DeploymentCommitDisposition -CandidateCommit 'a' -CurrentlyDeployedCommit 'b' -CandidateIsAncestorOfDeployed $true) 'already-deployed'
    Assert-ShouldBe (Get-DeploymentCommitDisposition -CandidateCommit 'b' -CurrentlyDeployedCommit 'a' -DeployedIsAncestorOfCandidate $true) 'deploy'
    Assert-ShouldBe (Get-DeploymentCommitDisposition -CandidateCommit 'b' -CurrentlyDeployedCommit 'a') 'reject'
  }

  It 'persists and replaces state without leaving partial temporary files' {
    $statePath = Join-Path $TestDrive 'state\issues.json'
    $state = @{ 'issue-42' = @{ stage = 'planning'; attempts = @{ planning = 1 } } }
    Save-IssueAgentState -Path $statePath -State $state

    $state['issue-42'].stage = 'coding'
    Save-IssueAgentState -Path $statePath -State $state
    $loaded = Read-IssueAgentState -Path $statePath

    Assert-ShouldBe $loaded['issue-42'].stage 'coding'
    Assert-ShouldBe @(Get-ChildItem -Path (Split-Path -Parent $statePath) -Filter '*.tmp').Count 0
  }
}

Describe 'Issue agent merge gate' {
  It 'allows deployment only for the recorded PR after an exact merge commit exists' {
    $issueState = @{ stage = 'awaiting-human-review'; prNumber = 42; issueNumber = 42; headBranch = 'agent/issue-42' }
    $pullRequest = @{
      state = 'MERGED'
      baseRefName = 'main'
      headRefName = 'agent/issue-42'
      isCrossRepository = $false
      mergeCommit = @{ oid = '0123456789012345678901234567890123456789' }
    }

    Assert-ShouldBe (Test-PullRequestDeploymentEligible -IssueState $issueState -PullRequest $pullRequest -DefaultBranch 'main') $true
    $retryIssueState = @{ stage = 'awaiting-human-review'; prNumber = 42; issueNumber = 42; headBranch = 'agent/issue-42-retry-0123456789abcdef0123456789abcdef' }
    $retryPullRequest = $pullRequest.Clone()
    $retryPullRequest.headRefName = $retryIssueState.headBranch
    Assert-ShouldBe (Test-PullRequestDeploymentEligible -IssueState $retryIssueState -PullRequest $retryPullRequest -DefaultBranch 'main') $true
  }

  It 'rejects unmerged, unrelated, or malformed PR results' {
    $issueState = @{ stage = 'awaiting-human-review'; prNumber = 42; issueNumber = 42 }
    $validCommit = '0123456789012345678901234567890123456789'
    $sameRepository = $false
    $notMerged = @{ state = 'OPEN'; baseRefName = 'main'; headRefName = 'agent/issue-42'; isCrossRepository = $sameRepository; mergeCommit = @{ oid = $validCommit } }
    $badCommit = @{ state = 'MERGED'; baseRefName = 'main'; headRefName = 'agent/issue-42'; isCrossRepository = $sameRepository; mergeCommit = @{ oid = 'not-a-commit' } }
    $wrongHead = @{ state = 'MERGED'; baseRefName = 'main'; headRefName = 'someone-else'; isCrossRepository = $sameRepository; mergeCommit = @{ oid = $validCommit } }
    $wrongBase = @{ state = 'MERGED'; baseRefName = 'release'; headRefName = 'agent/issue-42'; isCrossRepository = $sameRepository; mergeCommit = @{ oid = $validCommit } }
    $crossRepository = @{ state = 'MERGED'; baseRefName = 'main'; headRefName = 'agent/issue-42'; isCrossRepository = $true; mergeCommit = @{ oid = $validCommit } }
    $missingRepositoryStatus = @{ state = 'MERGED'; baseRefName = 'main'; headRefName = 'agent/issue-42'; mergeCommit = @{ oid = $validCommit } }
    $noRecordedPr = @{ stage = 'awaiting-human-review'; prNumber = 0; issueNumber = 42 }
    $alreadyDeployed = @{ stage = 'deployed'; prNumber = 42; issueNumber = 42 }

    Assert-ShouldBe (Test-PullRequestDeploymentEligible -IssueState $issueState -PullRequest $notMerged -DefaultBranch 'main') $false
    Assert-ShouldBe (Test-PullRequestDeploymentEligible -IssueState $issueState -PullRequest $badCommit -DefaultBranch 'main') $false
    Assert-ShouldBe (Test-PullRequestDeploymentEligible -IssueState $issueState -PullRequest $wrongHead -DefaultBranch 'main') $false
    Assert-ShouldBe (Test-PullRequestDeploymentEligible -IssueState $issueState -PullRequest $wrongBase -DefaultBranch 'main') $false
    Assert-ShouldBe (Test-PullRequestDeploymentEligible -IssueState $issueState -PullRequest $crossRepository -DefaultBranch 'main') $false
    Assert-ShouldBe (Test-PullRequestDeploymentEligible -IssueState $issueState -PullRequest $missingRepositoryStatus -DefaultBranch 'main') $false
    Assert-ShouldBe (Test-PullRequestDeploymentEligible -IssueState $noRecordedPr -PullRequest $wrongHead -DefaultBranch 'main') $false
    Assert-ShouldBe (Test-PullRequestDeploymentEligible -IssueState $alreadyDeployed -PullRequest $notMerged -DefaultBranch 'main') $false
  }
}
