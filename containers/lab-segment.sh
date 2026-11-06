#!/bin/bash
set -euo pipefail
export PG_RUN_ROLE=segmentation
exec "$(dirname -- "${BASH_SOURCE[0]}")/lab-run.sh" "$@"
