#!/bin/bash
# Wrapper de envío: se sitúa en la raíz del repo, crea slurm-logs/ y envía el job.
#
# Uso:
#   ./slurm/submit.sh <config.yaml> [escena] [args extra del pipeline...]
#
# Para sobreescribir recursos SLURM usar la variable SBATCH_OPTS:
#   SBATCH_OPTS="--gres=shard:rtxa6000:16 --mem=128G --time=24:00:00" \
#     ./slurm/submit.sh configs/experiments/x.yaml mi_escena

set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p slurm-logs

# shellcheck disable=SC2086  # SBATCH_OPTS debe expandirse por palabras
sbatch ${SBATCH_OPTS:-} slurm/pipeline.sbatch "$@"
