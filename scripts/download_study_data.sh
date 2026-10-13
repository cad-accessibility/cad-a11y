#!/usr/bin/env bash
# Download a study's data from a server: everything as one zip, or the long CSV.
#
# A script rather than a remembered curl line because this gets run again every
# time the analysis is re-run, and a command that is retyped each time is one
# that quietly differs each time: a wrong host, a stale file, or a proxy error
# page saved as data.
#
# The study's panel token is read from STUDY_TOKEN, or asked for without echoing
# it. It is sent in a header read from a private temporary file, so it is not in
# the URL, the shell history or the process list.
#
# Usage: scripts/download_study_data.sh <study-slug> [output-path]
# Env:   HOST         staging (default) | prod | a full https://host base URL
#        STUDY_TOKEN  the study's panel token; asked for when unset
#        CSV=1        the long CSV of completed sessions instead of the zip
#        SESSION      with CSV=1, one completed session id instead of all of them

set -euo pipefail

STAGING_URL="https://cada11y-test.cs.washington.edu"
PROD_URL="https://cada11y.cs.washington.edu"

fail() {
    echo "DOWNLOAD FAILED: $*" >&2
    exit 1
}

SLUG="${1:-}"
[ -n "$SLUG" ] || fail "say which study: scripts/download_study_data.sh <study-slug> [output-path]"
case "$SLUG" in
    *[!a-z0-9-]*) fail "a study slug is lowercase letters, digits and hyphens (got '${SLUG}')" ;;
esac

case "${HOST:-staging}" in
    staging|test)     BASE="$STAGING_URL" ;;
    prod|production)  BASE="$PROD_URL" ;;
    http://*|https://*) BASE="${HOST%/}" ;;
    *) fail "HOST must be 'staging', 'prod', or a full URL (got '${HOST}')" ;;
esac

stamp="$(date +%Y%m%d_%H%M%S)"
if [ -n "${CSV:-}" ]; then
    URL="${BASE}/studies/${SLUG}/control/export/long.csv"
    if [ -n "${SESSION:-}" ]; then
        URL="${URL}?session=${SESSION}"
    fi
    OUT="${2:-${SLUG}_long_${stamp}.csv}"
else
    URL="${BASE}/studies/${SLUG}/control/export/archive.zip"
    OUT="${2:-${SLUG}_${stamp}.zip}"
fi
[ ! -e "$OUT" ] || fail "${OUT} already exists; give another output path"

TOKEN="${STUDY_TOKEN:-}"
if [ -z "$TOKEN" ]; then
    read -rsp "Panel token for ${SLUG}: " TOKEN
    echo
fi
[ -n "$TOKEN" ] || fail "no token given"

# Downloaded to a temporary file and moved into place only once it has been
# checked. A half-written or wrong-content file left at the destination is worse
# than no file at all: it looks like data, and it would be analysed as data.
# mktemp creates both files readable only by this user, which is kept for the
# output too.
tmp="$(mktemp)"
headers="$(mktemp)"
trap 'rm -f "$tmp" "$headers"' EXIT
printf 'X-Study-Token: %s\n' "$TOKEN" > "$headers"

echo "Fetching ${URL}"
code="$(curl -sSL --max-time 600 -H @"$headers" -o "$tmp" -w '%{http_code}' "$URL")" \
    || fail "could not reach ${BASE}. On the lab network, or behind the VPN?"

if [ "$code" != "200" ]; then
    # 401 is a wrong token, 404 a study this server does not serve (or that has
    # no data here), 409 a session nobody finished. The message says which.
    echo "Server returned HTTP ${code}:" >&2
    head -c 500 "$tmp" >&2
    echo >&2
    exit 1
fi

# A 200 is not proof it is the data. A login page or a proxy error page is also
# a 200, and saving one of those under a data file's name is how a broken export
# gets noticed weeks later.
if [ -n "${CSV:-}" ]; then
    header="$(head -n 1 "$tmp")"
    case "$header" in
        participant_code,*) ;;
        *) fail "that is not the study CSV. The first line was: ${header:0:120}" ;;
    esac
    rows=$(( $(wc -l < "$tmp") - 1 ))
    mv "$tmp" "$OUT"
    trap 'rm -f "$headers"' EXIT
    echo "Wrote ${OUT} (${rows} rows)."
    if [ "$rows" -le 0 ]; then
        echo "Nothing has been completed yet, or the sessions you expected are still active or abandoned." >&2
    fi
else
    [ "$(head -c 2 "$tmp")" = "PK" ] || fail "that is not a zip. It began: $(head -c 120 "$tmp")"
    mv "$tmp" "$OUT"
    trap 'rm -f "$headers"' EXIT
    echo "Wrote ${OUT}. Its manifest.json lists every file with a SHA-256, and checks.json what the data checks found."
fi
