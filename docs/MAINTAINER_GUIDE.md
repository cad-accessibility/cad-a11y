# Maintainer Guide

This guide defines how maintainers triage issues, prepare releases,
and decide when pull requests are ready to merge in the cad-a11y
repository. At a high level, maintainers should prioritize
accessibility impact, reproducibility, and contributor experience.

## Branching Strategy

We use GitHub Flow: one protected `master` branch plus short-lived feature branches.

- `master` is always deployable and protected — no direct pushes allowed.
- All changes come in through pull requests from feature branches.
- Feature branches are named with a Conventional Commit prefix: `feat/`, `fix/`, `docs/`, `a11y/`, etc.
- After a PR is squash-merged, the feature branch is deleted.

**Staging deploy:** every merge to `master` automatically deploys to the staging server via the
GitHub Actions mirror workflow. See `docs/DEPLOYMENT.md` for details.

**Production deploy:** create a GitHub Release with a `v*` tag. The mirror workflow pushes the tag
to GitLab, which triggers the production deploy job.

**Branch naming examples:**
```
feat/witmotion-calibration-ui
fix/depth-announcement-concave-geometry
a11y/monarch-payload-timing
docs/trinkey-setup-guide
```

## Issue Triage

Triage is the process of reviewing newly opened issues to make sure they are actionable and correctly
categorized before a maintainer or contributor starts working on them.

### When an issue is opened

1. **Check completeness.** Confirm the issue template fields are filled in. If key information is missing
   (reproduction steps, OS, display device, sample file), ask for it before doing anything else.

2. **Add labels.** Apply one or more of these:
   - `bug` — something is broken
   - `enhancement` — new capability or improvement
   - `docs` — documentation gap or error
   - `accessibility` — directly affects BLV or other disabled users
   - `needs-triage` — newly opened, not yet reviewed (remove once triage is done)
   - `good first issue` — self-contained, well-scoped, suitable for a new contributor
   - `help wanted` — needs a contributor; maintainers are not actively working on it

3. **Set priority** based on user impact:
   - **High** — data loss, broken core conversion flow, serious accessibility regression
   - **Medium** — feature defect with a known workaround, quality issue with moderate impact
   - **Low** — nice-to-have enhancement, minor content or style issue

4. **Link related work.** Cross-reference related issues, PRs, or research papers when relevant.

## Release Process

### Pre-Release Checklist

1. Confirm CI is green on `master` (lint, tests, accessibility checks).
2. Confirm documentation is updated for any user-facing behavior changes.
3. Update `CHANGELOG.md` — move items from `[Unreleased]` to a new `[v0.x.y]` heading.
4. Verify accessibility-impacting changes include screen reader testing.

### Release Steps

1. **Verify staging.** Every merge to `master` auto-deploys to staging at
   https://cada11y-test.cs.washington.edu. Confirm the build deployed successfully by checking
   GitHub Actions > "Mirror to GitLab" and GitLab CI/CD > Pipelines. Do a quick manual check
   on staging to confirm the app is working and any accessibility changes behave as expected.

2. **Create the GitHub Release.** When staging looks good, go to GitHub > Releases > Draft a new release.
   Create a `v0.x.y` tag from `master`, paste in the changelog entries, and publish the release.

3. **Watch the production deploy.** Publishing the release triggers the mirror workflow, which pushes the
   tag to GitLab, which fires `deploy_research_prod`. Confirm the GitLab pipeline passes and the
   production server at https://cada11y.cs.washington.edu is updated.

4. **Close the milestone** and open the next one.

### Versioning

- Use semantic versioning:
  - MAJOR for incompatible API or workflow changes.
  - MINOR for backward-compatible features.
  - PATCH for backward-compatible bug fixes.

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

- Squash merge all pull requests to keep history readable — the PR title becomes the commit message.
- The `master` branch is configured to only allow squash merges.

## Incident and Rollback Guidance

- If a release introduces a serious defect, revert the PR on GitHub. The mirror re-deploys `master`
  to staging automatically; a new release tag re-deploys to production.
- Follow up with root-cause analysis and tests before re-landing the change.
- See `docs/DEPLOYMENT.md` for manual rollback procedures using your personal GitLab access.
