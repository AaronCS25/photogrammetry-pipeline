#!/bin/bash
# Sequential controlled comparison; stops immediately on any failed experiment.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCENE="${1:?Usage: bash containers/lab-segmentation-suite.sh SCENE [CONFIG_DIRECTORY]}"
CONFIGS="${2:-configs/experiments/segmentation-025-0-10s}"
for variant in control segformer sam3 combined; do
    bash "$HERE/lab-experiment.sh" --scene "$SCENE" --config "$CONFIGS/$variant.yaml"
done
