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
mirror-to-gitlab.yml (GitHub Actions) pushes master -> GitLab
        |
        v
GitLab CI: deploy_research_test fires -> staging server updated
```

```
GitHub Release created (tag v0.x.y)
        |
        v
mirror-to-gitlab.yml pushes the tag -> GitLab
        |
        v
GitLab CI: deploy_research_prod fires -> production server updated
```

## Credentials used by the automated mirror

The `mirror-to-gitlab.yml` GitHub Actions workflow authenticates to GitLab using a **dedicated SSH deploy key**
called `github-actions-mirror`. Here is the full credential chain:

1. **SSH key pair generated** — an Ed25519 key pair was generated specifically for this workflow.
   The private key is never stored in the repository.

2. **Public key registered on GitLab** — the public key is registered at
   `GitLab repo > Settings > Repository > Deploy keys` under the name `github-actions-mirror`
   with **write access enabled** (GitLab deploy key ID `69500`).
   Any machine holding the matching private key can push to this GitLab repository.

3. **Private key stored as a GitHub Actions secret** — the private key is stored in the GitHub repository
   at `Settings > Secrets and variables > Actions` under the name `GITLAB_SSH_KEY`.
   GitHub encrypts it at rest. It is only injected into GitHub Actions runner environments and is never
   printed in logs.

4. **GitLab URL stored as a GitHub Actions variable** — `git@gitlab.cs.washington.edu:make4all/cad-a11y.git`
   is stored as the variable `GITLAB_REPO_URL`.

5. **Workflow uses credentials at runtime** — when `mirror-to-gitlab.yml` runs, it:
   - Writes the private key from `GITLAB_SSH_KEY` into a temporary `~/.ssh/id_ed25519` on the runner
   - Adds `gitlab.cs.washington.edu` to `~/.ssh/known_hosts` via `ssh-keyscan`
   - Runs `git push gitlab master --follow-tags`
   - The runner is ephemeral; all credentials are gone when the job finishes

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

- The deploy key was revoked on GitLab — rotate it using the script below
- The `GITLAB_REPO_URL` variable is wrong — verify under GitHub repo Settings > Variables
- A `known_hosts` mismatch — update the `ssh-keyscan` line in `.github/workflows/mirror-to-gitlab.yml`

While the mirror is broken, maintainers can deploy manually using their own GitLab SSH access.

## Rotating the service deploy key

Use the script `scripts/replace-deploy-key.sh` to rotate the deploy key automatically.

**Dependencies** (install before running):
- `jq` — `brew install jq`
- `gh` — `brew install gh`, then `gh auth login`
- `ssh`, `ssh-keygen`, `curl` — already available on macOS and Linux

**Requirements before running:**
- Your personal SSH key must be registered on your GitLab account:
  `ssh -T git@gitlab.cs.washington.edu` should greet you by name
- The `gh` CLI must be logged into a GitHub account with write access to `cad-accessibility/cad-a11y`

```bash
bash scripts/replace-deploy-key.sh
```

The script will:
1. Generate a new Ed25519 key pair
2. Add the new public key to GitLab as `github-actions-mirror` (with write access)
3. Remove the old `github-actions-mirror` key from GitLab
4. Store the new private key as the `GITLAB_SSH_KEY` GitHub Actions secret
5. Update the `GITLAB_REPO_URL` variable
6. Clean up all temporary key files

It prompts for confirmation before making any changes.

## GitLab CI configuration reference

The file `.gitlab-ci.yml` defines two jobs:

- `deploy_research_test` — runs on every push to `master`; runs `docker compose up -d --build` on staging
- `deploy_research_prod` — runs on every `v*` tag; runs `docker compose up -d --build` on production

Do not edit `.gitlab-ci.yml` without understanding these implications.

## Contacts

If a CSE lab runner is offline or a server is unreachable, contact the lab system administrator.
The service deploy key is stored in GitHub repository secrets; do not share it via chat or email.
