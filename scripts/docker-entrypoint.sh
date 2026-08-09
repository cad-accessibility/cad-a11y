#!/bin/sh
# Repair data-directory ownership, then run the server as the app user.
#
# Why this exists: the five data directories are Docker named volumes, and
# Docker only applies the image's ownership to a volume while that volume is
# still empty. A volume that was created with the wrong owner keeps it through
# every subsequent rebuild, because seeding is skipped once it has content. On
# the CSE test host that is what happened to the logs volume: it is owned by
# root, the container runs as UID 48, and so the app cannot write to it.
#
# The app degrades rather than crashing (braille telemetry falls back to /tmp),
# which is why this stayed invisible from 22 July until /health started
# reporting it and the deploy gate began failing on 30 July. Fixing it requires
# a chown inside the mount, which needs root, which needs to happen before we
# drop to the app user. Hence an entrypoint rather than a Dockerfile USER line.
#
# This is a no-op on a correctly-owned deployment: each directory is only
# touched when its owner is already wrong.
set -eu

APP_USER=apache
DATA_ROOT=/project/data

# Fall back to the known UID if the account is somehow missing, so a broken
# passwd entry cannot silently leave the server running as root.
APP_UID="$(id -u "$APP_USER" 2>/dev/null || echo 48)"
APP_GID="$(id -g "$APP_USER" 2>/dev/null || echo 48)"

if [ "$(id -u)" = "0" ]; then
    for name in models uploads renders logs db; do
        dir="$DATA_ROOT/$name"

        # A volume whose mountpoint does not exist yet is not an error: Docker
        # creates it on mount, and the server also creates what it needs.
        mkdir -p "$dir" 2>/dev/null || true

        owner="$(stat -c '%u' "$dir" 2>/dev/null || echo unknown)"
        [ "$owner" = "$APP_UID" ] && continue

        echo "entrypoint: $dir is owned by UID $owner, expected $APP_UID; fixing" >&2
        # Recursive: files written by an earlier root-owned deployment stay
        # root-owned inside an otherwise-correct directory, and an append to
        # one of those (the braille JSONL, a study log) would still fail.
        if ! chown -R "$APP_UID:$APP_GID" "$dir" 2>/dev/null; then
            echo "entrypoint: WARNING could not chown $dir; /health will report it" >&2
        fi
    done

    # HOME would otherwise stay /root, which the app user cannot write. conda
    # and matplotlib both want a writable home and fail in confusing ways
    # without one. USER used to set this for us.
    HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
    [ -n "$HOME" ] || HOME=/home/"$APP_USER"
    export HOME

    # exec so the server becomes PID 1 and SIGTERM still reaches it, which is
    # what the exec-form ENTRYPOINT was protecting.
    exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --clear-groups "$@"
fi

# Already unprivileged (someone passed --user, or the image is being run
# directly): nothing to repair and nothing to drop.
exec "$@"
