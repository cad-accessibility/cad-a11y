#!/usr/bin/env bash
# Build and start the app, falling back to Dockerfile.legacy if this host's
# Docker is too old to support the BuildKit cache mount the main Dockerfile
# uses (`RUN --mount=type=cache`).
#
# There's no reliable way to predict this from `docker version` up front —
# BuildKit availability depends on daemon config as well as version, and a
# client can be new while the daemon it talks to is old — so instead of
# guessing, this tries the normal build first and only falls back on the
# exact failure a BuildKit-less Docker produces for that RUN flag. Without
# this, that failure is indistinguishable in a CI log from any other Step
# N/M build error unless you already know the phrase to look for.
#
# Usage: scripts/docker_compose_build.sh

set -uo pipefail

BUILDKIT_REQUIRED_MSG="the --mount option requires BuildKit"

log="$(mktemp)"
trap 'rm -f "$log"' EXIT

if docker compose up -d --build 2>&1 | tee "$log"; then
    exit 0
fi

if grep -qF "$BUILDKIT_REQUIRED_MSG" "$log"; then
    echo
    echo "BUILD FALLBACK: this Docker does not support BuildKit cache mounts" >&2
    echo "(RUN --mount=type=cache), which the main Dockerfile relies on." >&2
    echo "Retrying with Dockerfile.legacy — no cache mount, so rebuilds after" >&2
    echo "touching environment.yml re-download packages instead of reusing a cache." >&2
    echo
    exec env DOCKERFILE=Dockerfile.legacy docker compose up -d --build
fi

echo "Build failed for a reason unrelated to BuildKit support; see the log above." >&2
exit 1
