#!/usr/bin/env bash
# Download the completed study sessions as one long-format CSV.
#
# A script rather than a remembered curl line because this gets run again every
# time the quantitative analysis is re-run, and participants are still being
# added. A command that is retyped each time is one that quietly differs each
# time: a wrong host, a stale file, or a proxy error page saved as data.
#
# The endpoint covers completed sessions only. A session still running or one
# that was abandoned is not in the file, and asking for one by id says so.
#
# Usage: scripts/download_study_csv.sh [output-path]
# Env:   HOST     staging (default) | prod | a full https://host base URL
#        SESSION  one completed session id, instead of every completed session

set -euo pipefail

STAGING_URL="https://cada11y-test.cs.washington.edu"
PROD_URL="https://cada11y.cs.washington.edu"

fail() {
    echo "DOWNLOAD FAILED: $*" >&2
    exit 1
}

case "${HOST:-staging}" in
    staging|test)     BASE="$STAGING_URL" ;;
    prod|production)  BASE="$PROD_URL" ;;
    http://*|https://*) BASE="${HOST%/}" ;;
    *) fail "HOST must be 'staging', 'prod', or a full URL (got '${HOST}')" ;;
esac

URL="${BASE}/study/export/long.csv"
if [ -n "${SESSION:-}" ]; then
    URL="${URL}?session=${SESSION}"
fi

OUT="${1:-study_long_$(date +%Y%m%d_%H%M%S).csv}"

# Downloaded to a temporary file and moved into place only once it has been
# checked. A half-written or wrong-content file left at the destination is worse
# than no file at all: it looks like data, and it would be analysed as data.
# mktemp creates it readable only by this user, which is kept for the output too.
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

echo "Fetching ${URL}"
code="$(curl -sSL --max-time 120 -o "$tmp" -w '%{http_code}' "$URL")" \
    || fail "could not reach ${BASE}. On the lab network, or behind the VPN?"

if [ "$code" != "200" ]; then
    # The endpoint answers 409 for a session nobody finished and 404 for one that
    # does not exist, both with a message saying which. That is more use than the
    # status code on its own.
    echo "Server returned HTTP ${code}:" >&2
    head -c 500 "$tmp" >&2
    echo >&2
    exit 1
fi

# A 200 is not proof it is the CSV. A login page or a proxy error page is also a
# 200, and saving one of those under a .csv name is how a broken export gets
# noticed weeks later.
header="$(head -n 1 "$tmp")"
case "$header" in
    participant_code,*) ;;
    *) fail "that is not the study CSV. The first line was: ${header:0:120}" ;;
esac

rows=$(( $(wc -l < "$tmp") - 1 ))
mv "$tmp" "$OUT"
trap - EXIT

if [ "$rows" -le 0 ]; then
    echo "Wrote ${OUT}, header only, no rows."
    echo "Nothing has been completed yet, or the sessions you expected are still active or abandoned." >&2
else
    echo "Wrote ${OUT} (${rows} rows)."
fi
