#!/usr/bin/env bash
# Rotate the GitLab project access token used by the GitHub Actions mirror workflow.
#
# Dependencies:
#   curl  -- API calls (pre-installed on macOS and Linux)
#   jq    -- JSON parsing: brew install jq
#   gh    -- GitHub CLI: brew install gh, then: gh auth login
#   ssh   -- key-based GitLab auth (pre-installed on macOS and Linux)
#
# Requirements:
#   - Your personal SSH key must be registered on your GitLab account.
#     Test: ssh -T git@gitlab.cs.washington.edu
#   - gh must be authenticated with write access to cad-accessibility/cad-a11y.
#     Test: gh auth status

set -euo pipefail

GITLAB_HOST="gitlab.cs.washington.edu"
GITLAB_PROJECT="make4all%2Fcad-a11y"
GITHUB_REPO="cad-accessibility/cad-a11y"
TOKEN_NAME="github-actions-mirror"
TOKEN_SCOPE="write_repository"
EXPIRES_AT="$(date -v+1y '+%Y-%m-%d' 2>/dev/null || date -d '+1 year' '+%Y-%m-%d')"

TEMP_PAT=""

# Always revoke the temporary PAT on exit
cleanup() {
  if [[ -n "$TEMP_PAT" ]]; then
    curl -sS -X DELETE --header "PRIVATE-TOKEN: $TEMP_PAT" \
      "https://$GITLAB_HOST/api/v4/personal_access_tokens/self" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# Check dependencies
for cmd in curl jq gh ssh; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "Error: '$cmd' is not installed. See script header for install instructions." >&2
    exit 1
  fi
done

echo "=== GitLab mirror token rotation ==="
echo "Host:    $GITLAB_HOST"
echo "Project: make4all/cad-a11y"
echo "GitHub:  $GITHUB_REPO"
echo ""

# Verify SSH access to GitLab
echo "Verifying GitLab SSH access..."
if ! ssh -T "git@$GITLAB_HOST" 2>&1 | grep -q "Welcome"; then
  echo "Error: SSH authentication to $GITLAB_HOST failed." >&2
  echo "Make sure your SSH key is registered on your GitLab account." >&2
  exit 1
fi
echo "SSH access confirmed."
echo ""

echo "This script will:"
echo "  1. Create a new project access token '$TOKEN_NAME' on GitLab (expires $EXPIRES_AT)"
echo "  2. Store the new token as GitHub secret GITLAB_HTTPS_TOKEN"
echo "  3. Revoke the old '$TOKEN_NAME' token on GitLab"
echo ""
read -rp "Continue? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
echo ""

# Get a temporary personal access token via SSH
echo "Obtaining temporary GitLab API token..."
TEMP_PAT=$(ssh "git@$GITLAB_HOST" personal_access_token "rotate-script-temp" api 2>/dev/null \
  | grep '^Token:' | awk '{print $2}')
if [[ -z "$TEMP_PAT" ]]; then
  echo "Error: Failed to obtain a GitLab personal access token via SSH." >&2
  exit 1
fi
echo "Temporary token obtained (will be revoked on exit)."

# Find existing project access tokens with this name
echo "Looking up existing '$TOKEN_NAME' tokens..."
OLD_TOKEN_IDS=$(curl -sS --header "PRIVATE-TOKEN: $TEMP_PAT" \
  "https://$GITLAB_HOST/api/v4/projects/$GITLAB_PROJECT/access_tokens?per_page=100" \
  | jq -r --arg name "$TOKEN_NAME" '.[] | select(.name == $name and .revoked == false) | .id')

if [[ -z "$OLD_TOKEN_IDS" ]]; then
  echo "No existing active '$TOKEN_NAME' tokens found — will just create a new one."
else
  echo "Found existing token(s): $OLD_TOKEN_IDS"
fi

# Create new project access token
echo "Creating new project access token..."
NEW_TOKEN_JSON=$(curl -sS --fail-with-body -X POST \
  --header "PRIVATE-TOKEN: $TEMP_PAT" \
  --header "Content-Type: application/json" \
  --data "{\"name\": \"$TOKEN_NAME\", \"scopes\": [\"$TOKEN_SCOPE\"], \"expires_at\": \"$EXPIRES_AT\", \"access_level\": 40}" \
  "https://$GITLAB_HOST/api/v4/projects/$GITLAB_PROJECT/access_tokens")

NEW_TOKEN_VALUE=$(echo "$NEW_TOKEN_JSON" | jq -r '.token')
NEW_TOKEN_ID=$(echo "$NEW_TOKEN_JSON" | jq -r '.id')
NEW_TOKEN_EXPIRES=$(echo "$NEW_TOKEN_JSON" | jq -r '.expires_at')

if [[ -z "$NEW_TOKEN_VALUE" || "$NEW_TOKEN_VALUE" == "null" ]]; then
  echo "Error: Failed to create new project access token." >&2
  echo "$NEW_TOKEN_JSON" >&2
  exit 1
fi
echo "New token created (ID: $NEW_TOKEN_ID, expires: $NEW_TOKEN_EXPIRES)"

# Store in GitHub Actions secret
echo "Storing token as GitHub secret GITLAB_HTTPS_TOKEN..."
echo -n "$NEW_TOKEN_VALUE" | gh secret set GITLAB_HTTPS_TOKEN --repo "$GITHUB_REPO" --body -
echo "GitHub secret updated."

# Revoke old tokens
if [[ -n "$OLD_TOKEN_IDS" ]]; then
  echo "Revoking old token(s)..."
  while IFS= read -r old_id; do
    curl -sS -X DELETE --header "PRIVATE-TOKEN: $TEMP_PAT" \
      "https://$GITLAB_HOST/api/v4/projects/$GITLAB_PROJECT/access_tokens/$old_id" && \
      echo "  Revoked token ID $old_id"
  done <<< "$OLD_TOKEN_IDS"
fi

echo ""
echo "Done. The mirror workflow will use the new token on its next run."
echo "Token expires: $NEW_TOKEN_EXPIRES"
echo "To verify: gh run workflow mirror-to-gitlab.yml --repo $GITHUB_REPO"
