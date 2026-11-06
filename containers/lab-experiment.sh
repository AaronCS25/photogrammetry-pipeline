#!/bin/bash
# Run one immutable experiment across the two declared environments.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
bash "$HERE/lab-run.sh" run "$@" --stages frames,telemetry
bash "$HERE/lab-segment.sh" run "$@" --stages masks
bash "$HERE/lab-run.sh" run "$@" --stages sfm,undistort,dense,metrics
