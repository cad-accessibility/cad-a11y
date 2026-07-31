#!/usr/bin/env bash
# Check a running deployment over HTTP, from anywhere.
#
#     bash scripts/verify_deploy_state.sh                    # staging and production
#     bash scripts/verify_deploy_state.sh http://localhost:8635/
#
# We do not have shell access to the servers, so the app reports its own state
# at /health and this reads it. Every check here is something that has actually
# broken in production: the database the app could not open during the
# 2026-07-22 outage, and the storage layout that made uploads public and emptied
# the model list (#102).
#
# Read-only. Exits non-zero if any check fails, so it can gate a pipeline.

set -uo pipefail

DEFAULT_TARGETS=(
    https://cada11y-test.cs.washington.edu
    https://cada11y.cs.washington.edu
)
failures=0

ok()   { printf '  OK    %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; failures=$((failures + 1)); }

check_target() {
    local base="${1%/}"
    printf '\n%s\n' "$base"

    local body http
    body="$(curl -s --max-time 20 -w '\n%{http_code}' "$base/health" 2>/dev/null)" || {
        fail "unreachable"
        return
    }
    http="$(printf '%s' "$body" | tail -n1)"
    body="$(printf '%s' "$body" | sed '$d')"

    if [ "$http" = "404" ]; then
        fail "no /health endpoint — this deployment predates the self-check; deploy master and re-run"
        return
    fi
    if [ -z "$body" ]; then
        fail "empty response (HTTP $http)"
        return
    fi

    # jq keeps this readable, but the script should still work without it.
    if command -v jq >/dev/null 2>&1; then
        _q() { printf '%s' "$body" | jq -r "$1" 2>/dev/null; }
    else
        _q() { printf '%s' "$body" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print(''); raise SystemExit
p='$1'.lstrip('.').replace('\"','').split('.')
for k in p:
    if isinstance(d,dict) and k in d: d=d[k]
    else: d=''; break
print(str(d).lower() if isinstance(d,bool) else d)
" 2>/dev/null; }
    fi

    [ "$(_q '.status')" = "ok" ] && ok "overall status ok" || fail "overall status: $(_q '.status') (HTTP $http)"

    [ "$(_q '.checks.storage_separated')" = "true" ] \
        && ok "uploads stored separately from shipped models" \
        || fail "uploads share a directory with the shipped models — every upload is public (#102)"

    [ "$(_q '.checks.database')" = "ok" ] \
        && ok "database opens" \
        || fail "database: $(_q '.checks.database') — this is what caused the 2026-07-22 outage"

    local d
    for d in models uploads renders logs; do
        [ "$(_q ".checks.writable.$d")" = "true" ] && ok "$d directory writable" || fail "$d directory not writable by the container user"
    done

    local shipped public extra
    shipped="$(_q '.checks.builtin_models_shipped')"
    public="$(_q '.checks.public_models')"
    extra="$(_q '.checks.unexpected_public_models')"

    if [ "${shipped:-0}" -gt 0 ] 2>/dev/null; then
        ok "$shipped built-in model(s) shipped"
    else
        fail "no built-in models shipped — the image or the startup seed is wrong"
    fi

    if [ "${extra:-0}" -gt 0 ] 2>/dev/null; then
        ok "$public model(s) public"
        echo "        note: $extra of these did not ship with the app. They are left over"
        echo "        from before uploads were separated and are visible to everyone."
        echo "        Clear with: docker compose exec app python scripts/cleanup_ingest_models.py --apply"
    else
        ok "$public model(s) public, all of them shipped with the app"
    fi
}

targets=("$@")
[ ${#targets[@]} -eq 0 ] && targets=("${DEFAULT_TARGETS[@]}")

for t in "${targets[@]}"; do
    check_target "$t"
done

echo
if [ "$failures" -eq 0 ]; then
    echo "All checks passed."
else
    echo "$failures check(s) failed."
fi
exit $(( failures > 0 ))
