#!/bin/bash
# =============================================================================
# Job SLURM del pipeline de fotogrametría (Khipu).
#
# Uso (desde la raíz del repo, o mejor vía ./slurm/submit.sh):
#   sbatch slurm/pipeline.sbatch <config.yaml> [escena] [args extra del pipeline...]
#
# Ejemplos:
#   sbatch slurm/pipeline.sbatch configs/experiments/drone_2fps_1080p.yaml plaza_utec_v1
#   sbatch --gres=shard:rtxa6000:16 --mem=128G slurm/pipeline.sbatch \
#          configs/experiments/multisource_drone_phone.yaml fachada_norte
#   sbatch slurm/pipeline.sbatch configs/experiments/x.yaml escena --from-stage dense --force
#
# Los flags pasados a `sbatch` en la línea de comandos SOBREESCRIBEN las
# directivas #SBATCH de este archivo (partición, gres, memoria, tiempo...).
# =============================================================================

#SBATCH --job-name=photogram
#SBATCH --output=slurm-logs/%x-%j.out
#SBATCH --partition=gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --gres=shard:8
#SBATCH --time=12:00:00

## GPU específica (opcional): --gres=shard:tesla:8 | shard:rtxa6000:8 | shard:a100:8
## Notificaciones por correo (opcional):
##SBATCH --mail-type=END,FAIL
##SBATCH --mail-user=<tu-correo>

set -euo pipefail

CONFIG="${1:?Uso: sbatch slurm/pipeline.sbatch <config.yaml> [escena] [args extra...]}"
shift

SCENE_ARGS=()
if [[ $# -gt 0 && "$1" != --* ]]; then
    SCENE_ARGS=(--scene "$1")
    shift
fi

CONTAINER="${CONTAINER:-containers/photogrammetry.sif}"
if [[ ! -f "$CONTAINER" ]]; then
    echo "ERROR: no existe la imagen $CONTAINER (ver containers/README.md)" >&2
    exit 1
fi

echo "== Job $SLURM_JOB_ID en $(hostname) | config=$CONFIG ${SCENE_ARGS[*]:-} $*"
echo "== Inicio: $(date -Is)"

# --- Fase previa: enmascaramiento semántico (solo si la config lo habilita) ---
# La segmentación necesita torch, que vive en un contenedor aparte. Se ejecutan
# ahí las etapas frames/telemetry/masks (pre-masks respeta --stages/--from-stage/
# --force) y el run principal las encuentra completadas vía los marcadores.
MASKS_CONTAINER="${MASKS_CONTAINER:-containers/segmentation.sif}"
MASKING_ENABLED=$(PIPELINE_CFG="$CONFIG" apptainer exec "$CONTAINER" python3 -c \
    "import os; from pipeline.config import load_config; print((load_config(os.environ['PIPELINE_CFG']).get('masking') or {}).get('enabled', False))" \
    2>/dev/null || echo False)
if [[ "$MASKING_ENABLED" == "True" ]]; then
    if [[ ! -f "$MASKS_CONTAINER" ]]; then
        echo "ERROR: masking.enabled=true pero no existe $MASKS_CONTAINER (ver containers/README.md)" >&2
        exit 1
    fi
    echo "== Fase de enmascaramiento ($MASKS_CONTAINER)"
    apptainer exec --nv "$MASKS_CONTAINER" \
        python3 -m pipeline pre-masks --config "$CONFIG" "${SCENE_ARGS[@]}" "$@"
fi

# Sin `srun`: los gres de tipo `shard` se asignan al job pero los job steps no
# pueden heredarlos (srun falla con "Invalid generic resource specification").
# Con --ntasks=1 el comando corre igual en el nodo asignado.
apptainer exec --nv "$CONTAINER" \
    python3 -m pipeline run --config "$CONFIG" "${SCENE_ARGS[@]}" "$@"

echo "== Fin: $(date -Is)"
