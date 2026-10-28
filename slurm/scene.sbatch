#!/bin/bash
# Resource flags are supplied by pipeline.scheduler.slurm; no hidden GPU defaults.
set -euo pipefail
REQUEST="${1:?request.json}"
PHASE="${2:?phase}"
IMAGE="${3:?image.sif}"
GPU="${4:-0}"
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
python3 - "$REQUEST" "$PHASE" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    r = json.load(f)
print(json.dumps({"versioned_scene": True, "scene": r["scene"],
                  "experiment": r["experiment"], "request": sys.argv[1], "phase": sys.argv[2]}), flush=True)
PY
SCRATCH="/local/$USER/photogrammetry/${SLURM_JOB_ID:?}"
mkdir -p "$SCRATCH"
trap 'rm -rf -- "$SCRATCH"' EXIT
export TMPDIR="$SCRATCH"
OPTS=(--cleanenv --bind "$SCRATCH:$SCRATCH")
# Keep host Conda/PYTHONPATH/LD_LIBRARY_PATH out of the image; explicitly pass
# only the allocation metadata and thread budgets used by the pipeline.
for KEY in TMPDIR PYTHONUNBUFFERED OMP_NUM_THREADS OPENBLAS_NUM_THREADS MKL_NUM_THREADS \
           SLURM_JOB_ID SLURM_CPUS_PER_TASK SLURM_MEM_PER_NODE SLURM_JOB_GRES; do
    if [[ -v "$KEY" ]]; then OPTS+=(--env "$KEY=${!KEY}"); fi
done
if [[ "$GPU" == 1 ]]; then OPTS+=(--nv); fi
# Shard job steps cannot inherit gres on Khipu: execute in the allocated batch.
apptainer exec "${OPTS[@]}" "$IMAGE" \
    python3 -m pipeline scene-worker --request "$REQUEST" --phase "$PHASE"
