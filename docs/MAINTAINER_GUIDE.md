# Maintainer Guide

This guide defines how maintainers triage issues, prepare releases,
and decide when pull requests are ready to merge in the cad-a11y
repository. At a high level, maintainers should prioritize
accessibility impact, reproducibility, and contributor experience.

## Triage Process

### Initial Triage Checklist

1. Confirm the issue template is complete and understandable.
2. Add labels: bug, enhancement, docs, accessibility, needs-triage, good first issue, help wanted.
3. Set priority based on user impact and severity.
4. Request missing reproduction details when needed.
5. Link related issues, pull requests, and papers when applicable.

### Priority Guidelines

- High: Data loss, broken core conversion flow, serious accessibility regressions.
- Medium: Feature defects with workarounds, quality issues with moderate impact.
- Low: Nice-to-have enhancements, minor content or style issues.

## Release Process

### Pre-Release Checklist

1. Confirm branch protection requirements are passing.
2. Ensure CI is green (lint, tests, accessibility checks).
3. Confirm documentation updates for user-facing behavior changes.
4. Confirm changelog/release notes entries are complete.
5. Publish to test server (currently requires pushing to test in CSE Gitlab)
6. Verify accessibility-impacting changes include screen reader testing.

### Versioning

- Use semantic versioning:
  - MAJOR for incompatible API or workflow changes.
  - MINOR for backward-compatible features.
  - PATCH for backward-compatible bug fixes.

### Release Steps

1. Create a release branch if needed.
2. Finalize release notes from merged pull requests.
3. Tag the release in GitHub (for example v0.4.0).
4. Publish the GitHub Release with highlights and migration notes.
6. When satisfied with release, publish to main server (currently requires pushing to main and adding a tag in CSE Gitlab)
5. Close milestone and open the next one.

## Ready-To-Merge Criteria

A pull request is ready to merge only when all items below are true:

1. CI checks pass (lint, tests, and axe workflow).
2. At least one maintainer approval is present.
3. Review comments are resolved.
4. Scope is clear and limited (no hidden unrelated changes).
5. Tests are added or updated when behavior changes.
6. Docs are updated for user-visible changes.
7. Accessibility impact is described and acceptable.
8. No secrets, generated artifacts, or large unintended binaries were introduced.

## Merge Strategy

- Prefer squash merge for most pull requests to keep history readable.
- Use rebase merge for stacked or intentionally atomic commit histories.

## Incident and Rollback Guidance

- If a release introduces a serious defect, create a hotfix branch.
- Revert quickly, then follow up with root-cause analysis and tests.
