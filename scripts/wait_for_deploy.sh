#!/usr/bin/env bash
# Fail a deploy that did not actually come up.
#
# `docker compose up -d --build` returns as soon as the container is created, so
# a deploy that ends there reports success even when the app cannot start. That
# is how the 2026-07-22 staging outage reached users: the pipeline was green and
# a person found the 503.
#
# Two separate checks, because they fail for different reasons:
#   1. the container's own healthcheck, which catches an app that will not start
#   2. the public URL, which additionally catches a broken proxy in front of it
#
# Usage: scripts/wait_for_deploy.sh [public-url]
# Env:   SERVICE (default: app), TIMEOUT_SECONDS (default: 180)

set -euo pipefail

PUBLIC_URL="${1:-}"
SERVICE="${SERVICE:-app}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-180}"

fail() {
    echo "DEPLOY CHECK FAILED: $*" >&2
    echo "--- last 50 log lines from '$SERVICE' ---" >&2
    docker compose logs --tail=50 "$SERVICE" >&2 2>/dev/null || true
    exit 1
}

container_id="$(docker compose ps -q "$SERVICE" 2>/dev/null || true)"
[ -n "$container_id" ] || fail "no container found for service '$SERVICE'"

echo "Waiting up to ${TIMEOUT_SECONDS}s for '$SERVICE' to report healthy..."
deadline=$(( SECONDS + TIMEOUT_SECONDS ))
while :; do
    # Containers without a healthcheck report no Health field; treat a running
    # one as acceptable rather than blocking the deploy forever.
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id" 2>/dev/null || echo missing)"
    running="$(docker inspect --format '{{.State.Running}}' "$container_id" 2>/dev/null || echo false)"

    case "$health" in
        healthy) echo "Container healthy."; break ;;
        unhealthy) fail "container healthcheck reports unhealthy" ;;
        none) [ "$running" = "true" ] && { echo "No healthcheck defined; container is running."; break; } ;;
        missing) fail "container disappeared after deploy" ;;
    esac

    [ "$running" = "true" ] || fail "container is not running (health: $health)"
    (( SECONDS < deadline )) || fail "timed out after ${TIMEOUT_SECONDS}s (health: $health)"
    sleep 5
done

if [ -n "$PUBLIC_URL" ]; then
    # /health, not the root: the root answers even when storage is misconfigured
    # or the database will not open, so probing it proves only that something is
    # listening. /health returns 503 in exactly those cases.
    probe="${PUBLIC_URL%/}/health"
    echo "Probing $probe ..."
    deadline=$(( SECONDS + 60 ))
    while :; do
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$probe" || echo 000)"
        if [ "$code" = "200" ]; then
            echo "$probe returned 200."
            break
        fi
        # A deployment older than the self-check has no /health; fall back to the
        # root rather than failing a deploy for a missing endpoint.
        if [ "$code" = "404" ]; then
            code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$PUBLIC_URL" || echo 000)"
            [ "$code" = "200" ] && { echo "No /health on this build; root returned 200."; break; }
        fi
        (( SECONDS < deadline )) || fail "$probe returned $code, expected 200"
        sleep 5
    done
fi

echo "Deploy check passed."
