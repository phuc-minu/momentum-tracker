# Repository Agent Instructions

These instructions apply to all work in this repository and its descendant folders.

## Roles and authorization

When a Supervisor/Developer workflow is being used, the Supervisor plans and reviews work, while the Developer implements the approved plan.

The Developer must not modify project files without an approved implementation plan in the task brief. If no approved plan is supplied, stop and report that approval is required.

The Supervisor must obtain explicit human approval before implementation and explicit human code approval before publishing. Plan approval does not constitute code approval.

## Supervisor Responsibilities

- Research requirements and prepare a versioned implementation plan.
- Identify acceptance criteria, affected files, risks, dependencies, and exact build/test commands.
- Inspect Git status, branch, and existing changes before preparing work.
- Delegate implementation and tests to the Developer.
- Independently inspect the resulting diff and validation results.
- Review correctness, regressions, security, test coverage, unrelated changes, and secrets.
- Pause for human review when validation passes.
- Commit, push, and create a pull request only after explicit approval of the current code.

The Supervisor may write task briefs and review reports, but should not implement application code or tests directly when the delegated workflow is in use.

## Implementation

- Implement only the approved scope.
- Follow the existing architecture and coding conventions.
- Preserve unrelated user changes.
- Add or update meaningful tests for changed behavior.
- Do not modify workflow instructions or approval requirements.
- Do not introduce unrelated refactoring.
- Never overwrite unrelated user changes.
- Keep code simple for future maintenance.
- Do not add code that will create security or vulnerabilities.

If the approved scope must materially change, stop and request a revised plan and approval.

## Validation

- Run the exact build and test commands specified in the task brief.
- Inspect command exit codes and actual test counts.
- Fix failures caused by the implementation.
- Do not disable, weaken, or skip required tests to obtain success.
- Report missing dependencies and environmental blockers honestly.
- A zero-test result or a successful delegated command alone is not proof of correctness.

The Supervisor must independently review the implementation and rerun the required checks before presenting the work for human approval.

## Git and publishing restrictions

- Work on the branch prepared or agreed by the Supervisor.
- Do not switch branches during implementation.
- Do not stage, commit, push, or create or merge pull requests before explicit human code approval.
- Do not reset, clean, stash, or discard unrelated work.
- Stage only explicitly identified task files after approval.
- Exclude local coordination artifacts and unrelated changes from application commits.
- Never merge, enable auto-merge, force-push, or push directly to a protected base branch.

## Coordination artifacts

Use `.agent-work/` for local task briefs, logs, and reports when the Supervisor/Developer workflow requires them. Do not include these coordination artifacts in application commits.

## Final report

Report:

1. Summary of implementation.
2. Files changed.
3. Acceptance criteria satisfied or outstanding.
4. Exact commands executed and their outcomes.
5. Test counts and failures, when available.
6. Remaining risks or blockers.

Leave implementation changes available for Supervisor and human review until approval is obtained.
