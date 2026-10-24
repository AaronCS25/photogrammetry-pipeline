#!/bin/bash
# Ejecuta un comando dentro de la imagen Docker del pipeline (fuera de Khipu),
# con el repositorio montado en /work y la GPU NVIDIA si está disponible.
#
# Uso (desde cualquier carpeta):
#   ./containers/docker-run.sh python3 -m pipeline validate --config <cfg> --scene <escena>
#   ./containers/docker-run.sh python3 -m pipeline run      --config <cfg> --scene <escena>
#   ./containers/docker-run.sh colmap -h
#
# Variables opcionales:
#   IMAGE=photogrammetry:local   imagen a usar (la que construye containers/Dockerfile)
#   GPU=0                        no pedir GPU (por defecto se pide si hay nvidia-smi en el host)
#
# Khipu NO usa este script (allí: slurm/submit.sh + Apptainer).
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${IMAGE:-photogrammetry:local}"

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: 'docker' no está instalado o no está en el PATH." >&2
    echo "       Ver docs/ejecutar-fuera-de-khipu.md, paso 1." >&2
    exit 1
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "ERROR: la imagen '$IMAGE' no existe. Construirla primero (una sola vez, ~1-2 h):" >&2
    echo "       docker build -t $IMAGE -f containers/Dockerfile ." >&2
    exit 1
fi

GPU_OPTS=()
if [[ "${GPU:-1}" != "0" ]] && command -v nvidia-smi >/dev/null 2>&1; then
    GPU_OPTS=(--gpus all)
else
    echo "[docker-run] sin GPU: usar un YAML con 'runtime: {gpu: false}' (solo frames/sfm/undistort)" >&2
fi

# -u: los archivos creados en outputs/ quedan de tu usuario, no de root.
# -e HOME: algunos programas escriben caché en $HOME; /tmp siempre es escribible.
exec docker run --rm -it "${GPU_OPTS[@]}" \
    -u "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$PWD":/work -w /work \
    "$IMAGE" "$@"
