#!/bin/bash
# L507 pilot entry point; local settings stay outside Git and credentials are not needed.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PG_ENV_FILE="${PG_ENV_FILE:-/data/photogrammetry/node.env}"
[[ -r "$PG_ENV_FILE" ]] || { echo "Falta $PG_ENV_FILE; abrir/preparar el volumen primero." >&2; exit 1; }
exec "$HERE/docker-run.sh" python3 -m pipeline "$@"
