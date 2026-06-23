# Deployment Guide

This document describes how code moves from a merged pull request to a running server.
It is written for maintainers. Contributors who only open pull requests do not need to read this.

## Quick summary

- Merging a PR to `master` on GitHub automatically deploys to **staging**.
- Creating a GitHub Release (a `v*` tag) automatically deploys to **production**.
- No one needs to touch GitLab manually for normal releases. The bridge is already configured.

## Environments

| Environment | URL | Trigger |
|---|---|---|
| Staging | https://cada11y-test.cs.washington.edu | Every merge to `master` on GitHub |
| Production | https://cada11y.cs.washington.edu | Every `v*` tag / GitHub Release |

## How it works

GitHub is the source of truth for code. The CSE lab machines run the actual servers via GitLab CI.
A GitHub Actions workflow (`mirror-to-gitlab.yml`) bridges the two: it pushes `master` and release tags
from GitHub to GitLab after every merge, which triggers the GitLab CI deploy jobs.

```
PR merged to master on GitHub
        |
        v
mirror-to-gitlab.yml (GitHub Actions) pushes master -> GitLab via HTTPS
        |
        v
GitLab CI: deploy_research_test fires -> staging server updated
```

```
GitHub Release created (tag v0.x.y)
        |
        v
mirror-to-gitlab.yml pushes the tag -> GitLab via HTTPS
        |
        v
GitLab CI: deploy_research_prod fires -> production server updated
```

## Credentials used by the automated mirror

The `mirror-to-gitlab.yml` GitHub Actions workflow authenticates to GitLab using a **project access token**
called `github-actions-mirror`. SSH is not used because the UW CS GitLab server blocks authentication
from GitHub Actions runner IPs (Azure data-center ranges) on port 22.

The credential chain:

1. **Project access token created on GitLab** — a project-scoped token with `write_repository` access
   was created at `GitLab repo > Settings > Access Tokens` under the name `github-actions-mirror`
   (current token ID: `10330`, expires: 2027-06-23).
   It can push to this repository and nothing else.

2. **Token stored as a GitHub Actions secret** — the token value is stored in the GitHub repository
   at `Settings > Secrets and variables > Actions` under the name `GITLAB_HTTPS_TOKEN`.
   GitHub encrypts it at rest. It is only injected into GitHub Actions runner environments and is never
   printed in logs.

3. **Workflow uses the token at runtime** — when `mirror-to-gitlab.yml` runs, it pushes via HTTPS:
   ```
   https://oauth2:<GITLAB_HTTPS_TOKEN>@gitlab.cs.washington.edu/make4all/cad-a11y.git
   ```
   The runner is ephemeral; all credentials are gone when the job finishes.

This is a **service-account credential** not tied to any individual person's GitLab account.
Individual maintainers use their own personal SSH keys for manual deployments (see below).

## Manual deployment (using your own GitLab access)

If you need to deploy outside the normal PR-merge flow — for example to roll back quickly or test a hotfix
without going through a full PR — you can push directly to GitLab using your personal credentials.

### One-time setup per maintainer

Add GitLab as a second remote in your local clone:

```bash
git remote add gitlab git@gitlab.cs.washington.edu:make4all/cad-a11y.git
```

Verify your SSH key has push access:

```bash
ssh -T git@gitlab.cs.washington.edu
# Expected: Welcome to GitLab, @yourusername!
```

If this fails, add your SSH public key to your GitLab account at `User Settings > SSH Keys`.

### Deploy to staging

```bash
git push gitlab master
```

### Deploy a release to production

```bash
git push gitlab v0.x.y
```

### Roll back staging

```bash
git push gitlab <good-commit-sha>:refs/heads/master --force-with-lease
```

### Roll back production

```bash
git push gitlab v0.3.0   # last known-good tag
```

## If the automated mirror breaks

Check GitHub Actions > "Mirror to GitLab" for the failure reason. Common causes:

- The project access token expired or was revoked — rotate it using the script below
- The token stored in `GITLAB_HTTPS_TOKEN` does not match the active token on GitLab — re-run the rotation script
- GitLab HTTPS is unreachable from GitHub Actions — check `gitlab.cs.washington.edu` status

While the mirror is broken, maintainers can deploy manually using their own GitLab SSH access.

## Rotating the mirror credentials

Use the script `scripts/replace-deploy-key.sh` to rotate the project access token automatically.

**Dependencies** (install before running):
- `jq` — `brew install jq`
- `gh` — `brew install gh`, then `gh auth login`
- `curl`, `ssh` — already available on macOS and Linux

**Requirements before running:**
- Your personal SSH key must be registered on your GitLab account:
  `ssh -T git@gitlab.cs.washington.edu` should greet you by name
- The `gh` CLI must be logged into a GitHub account with write access to `cad-accessibility/cad-a11y`

```bash
bash scripts/replace-deploy-key.sh
```

The script will:
1. Use your personal GitLab SSH key to obtain a short-lived API token
2. Create a new project access token called `github-actions-mirror` (expires in 1 year)
3. Store the new token as the `GITLAB_HTTPS_TOKEN` GitHub Actions secret
4. Revoke the old `github-actions-mirror` token on GitLab
5. Revoke the short-lived API token

It prompts for confirmation before making any changes.

## GitLab CI configuration reference

The file `.gitlab-ci.yml` defines two jobs:

- `deploy_research_test` — runs on every push to `master`; runs `docker compose up -d --build` on staging
- `deploy_research_prod` — runs on every `v*` tag; runs `docker compose up -d --build` on production

Do not edit `.gitlab-ci.yml` without understanding these implications.

## Contacts

If a CSE lab runner is offline or a server is unreachable, contact the lab system administrator.
The mirror token is stored in GitHub repository secrets; do not share it via chat or email.
