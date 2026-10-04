#!/bin/sh
# Mounted volumes (docker compose, Fly.io) are created root-owned, so fix the
# ownership of the upload directory and then drop privileges before starting.
set -e

if [ "$(id -u)" = "0" ]; then
    upload_dir="${UPLOAD_DIR:-/data/uploads}"
    mkdir -p "$upload_dir"
    chown -R examable:examable "$upload_dir"
    exec setpriv --reuid=examable --regid=examable --init-groups "$@"
fi

exec "$@"
