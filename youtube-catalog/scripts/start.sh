#!/bin/sh
set -eu
python /app/scripts/storage.py check
exec "$@"
