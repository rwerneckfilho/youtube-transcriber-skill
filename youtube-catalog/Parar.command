#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
docker compose stop
echo "Catálogo desligado. A fila e os conteúdos continuam salvos no volume local do Docker."
