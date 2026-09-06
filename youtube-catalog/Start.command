#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
if ! docker info >/dev/null 2>&1; then
  echo "Open Docker Desktop and wait until it is ready. Then open this shortcut again."
  read -r -p "Press Enter to close. " || true
  exit 1
fi
if [ ! -f .env ]; then
  cp .env.example .env
fi
docker compose up -d
catalog_port=$(awk -F= '$1 == "CATALOG_PORT" {print $2}' .env)
catalog_url="http://localhost:${catalog_port:-8765}"
echo "Catalog started at $catalog_url"
for attempt in {1..30}; do
  if curl --silent --fail "$catalog_url/api/health" >/dev/null; then
    open "$catalog_url"
    exit 0
  fi
  sleep 1
done
echo "The catalog is still preparing the library. Open $catalog_url in a moment."
