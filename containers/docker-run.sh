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
#   PG_DATA_ROOT=/data/photogrammetry  datos/outputs fuera del repositorio
#   PG_REQUIRE_MOUNT=/data            rechaza escribir en / si falta el volumen
#   PG_ENV_FILE=/ruta/node.env        configuración local (no versionada)
#
# Khipu NO usa este script (allí: slurm/submit.sh + Apptainer).
set -euo pipefail
cd "$(dirname "$0")/.."

# Only source a user-selected, trusted local environment file.
if [[ -n "${PG_ENV_FILE:-}" ]]; then
    set -a
    source "$PG_ENV_FILE"
    set +a
fi

IMAGE="${IMAGE:-photogrammetry:local}"

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: 'docker' no está instalado o no está en el PATH." >&2
    echo "       Ver docs/ejecutar-fuera-de-khipu.md, paso 1." >&2
    exit 1
fi

if [[ -n "${PG_REQUIRE_MOUNT:-}" ]] && ! mountpoint -q "$PG_REQUIRE_MOUNT"; then
    echo "ERROR: volumen $PG_REQUIRE_MOUNT sin montar. Abrirlo antes de ejecutar Docker." >&2
    exit 1
fi
DATA_OPTS=()
if [[ -n "${PG_DATA_ROOT:-}" ]]; then
    PG_DATA_ROOT="$(realpath -e "$PG_DATA_ROOT")"
    [[ -d "$PG_DATA_ROOT/scratch" && -w "$PG_DATA_ROOT/scratch" ]] || {
        echo "ERROR: falta scratch escribible bajo PG_DATA_ROOT." >&2; exit 1;
    }
    if [[ -n "${PG_REQUIRE_MOUNT:-}" ]] && [[ "$(stat -c %d "$PG_DATA_ROOT")" != "$(stat -c %d "$PG_REQUIRE_MOUNT")" ]]; then
        echo "ERROR: datos fuera del volumen requerido." >&2; exit 1
    fi
    DATA_OPTS=(--mount "type=bind,src=$PG_DATA_ROOT,dst=$PG_DATA_ROOT"
        -e "PG_DATA_ROOT=$PG_DATA_ROOT" -e "PG_MIN_FREE_GIB=${PG_MIN_FREE_GIB:-20}"
        -e "TMPDIR=$PG_DATA_ROOT/scratch" -e "XDG_CACHE_HOME=$PG_DATA_ROOT/scratch/cache")
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "ERROR: la imagen '$IMAGE' no existe. Construirla primero (una sola vez, ~1-2 h):" >&2
    echo "       docker build -t $IMAGE -f containers/Dockerfile ." >&2
    exit 1
fi

GPU_OPTS=()
if [[ "${GPU:-1}" != "0" ]] && nvidia-smi >/dev/null 2>&1; then
    GPU_OPTS=(--gpus all)
else
    echo "[docker-run] sin GPU: usar un YAML con 'runtime: {gpu: false}' (solo frames/sfm/undistort)" >&2
fi

TTY_OPTS=()
if [[ -t 0 && -t 1 ]]; then TTY_OPTS=(-it); fi
RESOURCE_OPTS=()
if [[ -n "${PG_CPUS:-}" ]]; then RESOURCE_OPTS+=(--cpus "$PG_CPUS"); fi
if [[ -n "${PG_MEMORY:-}" ]]; then RESOURCE_OPTS+=(--memory "$PG_MEMORY"); fi
IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE")"
if [[ -n "${PG_EXPECTED_IMAGE_ID:-}" && "$IMAGE_ID" != "$PG_EXPECTED_IMAGE_ID" ]]; then
    echo "ERROR: la imagen no coincide con el ID certificado en node.env." >&2
    exit 1
fi
ENTRYPOINT_OPTS=()
if [[ "${PG_QUIET_ENTRYPOINT:-0}" == "1" ]]; then
    # Avoid the CUDA welcome banner ahead of machine-readable doctor JSON.
    ENTRYPOINT_OPTS=(--entrypoint /usr/bin/env)
    if [[ $# -eq 0 ]]; then set -- python3 -m pipeline --help; fi
fi

# -u: los archivos creados en outputs/ quedan de tu usuario, no de root.
# -e HOME: algunos programas escriben caché en $HOME; /tmp siempre es escribible.
exec docker run --rm "${TTY_OPTS[@]}" "${GPU_OPTS[@]}" "${DATA_OPTS[@]}" "${RESOURCE_OPTS[@]}" "${ENTRYPOINT_OPTS[@]}" \
    --init --label photogrammetry.project=pipeline --label "photogrammetry.node=${PG_NODE_TAG:-local}" \
    -u "$(id -u):$(id -g)" -e HOME=/tmp \
    -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONUNBUFFERED=1 -e "PG_IMAGE_ID=$IMAGE_ID" \
    -e "PG_NODE_TAG=${PG_NODE_TAG:-local}" \
    -v "$PWD":/work -w /work \
    "$IMAGE" "$@"
