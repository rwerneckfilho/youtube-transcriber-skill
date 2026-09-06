#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
docker compose stop
echo "Catalog stopped. The queue and content remain saved in the local Docker volume."
