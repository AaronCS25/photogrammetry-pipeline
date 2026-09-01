# Photogrammetry Pipeline — Video → Nube de puntos densa → Malla

Pipeline reproducible de fotogrametría para el cluster **Khipu (UTEC)**:
a partir de videos (dron y/o teléfono) genera frames con **FFmpeg**, reconstrucción
sparse con **COLMAP**, y nube densa + malla + textura con **OpenMVS**, todo
controlado por archivos de configuración **YAML** y ejecutado dentro de un
contenedor **Apptainer** vía **SLURM**.

```
video.mp4 ──ffmpeg──▶ frames ──COLMAP──▶ sparse ──undistort──▶ scene.mvs
                                                                 │ OpenMVS
                     scene_dense.ply ◀──DensifyPointCloud────────┘
                     scene_mesh.ply  ◀──ReconstructMesh
                     scene_mesh_refined.ply ◀──RefineMesh
                     scene_texture.obj ◀──TextureMesh
```

## Estructura del repositorio

```
photogrammetry-pipeline/
├── configs/
│   ├── default.yaml            # Configuración base (documentada) — NO editar por experimento
│   └── experiments/            # Un YAML por experimento (solo overrides)
├── containers/
│   ├── photogrammetry.def      # Definición Apptainer (FFmpeg + COLMAP + OpenMVS, CUDA)
│   └── README.md               # Cómo construir la imagen en Khipu
├── datasets/
│   ├── raw/                    # ⇐ AQUÍ van tus videos (ver datasets/README.md)
│   └── README.md
├── docs/
│   ├── khipu-workflow.md       # Guía paso a paso en Khipu (clone → build → sbatch → resultados)
│   └── configuration.md        # Referencia completa de configuración + recetas
├── outputs/                    # Resultados por escena/experimento (no se versiona)
├── pipeline/                   # Orquestador Python (stdlib + PyYAML)
├── slurm/
│   ├── pipeline.sbatch         # Job SLURM parametrizado
│   └── submit.sh               # Wrapper de envío
└── README.md
```

## Inicio rápido (en Khipu)

```bash
# 1. Clonar (el nodo maestro tiene internet)
git clone <URL-de-este-repo>
cd photogrammetry-pipeline

# 2. Construir el contenedor UNA sola vez (~1.5-2 h, nodo maestro, dentro de tmux)
tmux new -s build
cd containers
apptainer build base.sif base.def 2>&1 | tee build-base.log                 # deps + COLMAP
apptainer build photogrammetry.sif photogrammetry.def 2>&1 | tee build.log  # + OpenMVS
cd ..    # (Ctrl+B, D para salir de tmux dejándolo correr)

# 3. Subir tus videos (desde tu laptop)
#    rsync -avP ./mi_escena/ khipu:~/photogrammetry-pipeline/datasets/raw/mi_escena/

# 4. Crear tu experimento copiando un ejemplo
cp configs/experiments/drone_2fps_1080p.yaml configs/experiments/mi_experimento.yaml

# 5. Validar sin gastar cómputo (nodo maestro, sin GPU)
apptainer exec containers/photogrammetry.sif \
    python3 -m pipeline validate --config configs/experiments/mi_experimento.yaml --scene mi_escena

# 6. Enviar el job
./slurm/submit.sh configs/experiments/mi_experimento.yaml mi_escena

# 7. Monitorear
squeue -u $USER
tail -f slurm-logs/photogram-<jobid>.out
```

Los resultados quedan en `outputs/<escena>/<experimento>/`:

```
outputs/mi_escena/mi_experimento/
├── frames/<fuente>/            # Frames extraídos (drone/, phone/, o main/)
├── telemetry/                  # CSV parseado de los .srt del dron
├── colmap/                     # database.db, sparse/, undistorted/
├── mvs/                        # scene.mvs, scene_dense.ply, scene_mesh.ply, scene_texture.obj ...
├── metrics/                    # metrics.json, timings.csv, salida de model_analyzer
├── logs/                       # Log por etapa
├── config_resolved.yaml        # Config exacta usada (reproducibilidad)
└── .stages/                    # Marcadores de etapas completadas (permite reanudar)
```

## Datos de entrada

```
datasets/raw/<escena>/
├── drone/          # *.MP4 (+ *.SRT opcional con telemetría DJI)
└── phone/          # *.mp4 del teléfono
```

Si la escena tiene una sola fuente, los videos pueden ir directamente en
`datasets/raw/<escena>/` (se tratan como fuente `main`). Cada subcarpeta se
modela en COLMAP como una cámara distinta (`single_camera_per_folder`), que es
exactamente lo que se necesita al combinar dron + teléfono.

> Por ahora el pipeline soporta **solo video** como entrada. El soporte de fotos
> sueltas (modo ráfaga del teléfono) está previsto como extensión.

## Etapas del pipeline

| Etapa | Herramienta | Salida |
|---|---|---|
| `frames` | ffmpeg | frames JPG/PNG a los fps y resolución configurados |
| `telemetry` | parser propio | CSV con lat/lon/alt por frame (si hay .srt) |
| `sfm` | colmap feature_extractor + matcher + mapper | modelo sparse |
| `undistort` | colmap image_undistorter | imágenes sin distorsión + workspace denso |
| `dense` | OpenMVS DensifyPointCloud (o COLMAP PatchMatch) | nube de puntos densa (.ply) |
| `mesh` | OpenMVS ReconstructMesh (+ RefineMesh) | malla (.ply) |
| `texture` | OpenMVS TextureMesh | malla texturizada (.obj/.ply) |
| `metrics` | model_analyzer + parsers | metrics.json, timings.csv |

Todas las etapas son **reanudables**: si un job muere en `dense`, el siguiente
run se salta lo ya completado. `--force` re-ejecuta, `--stages` y `--from-stage`
seleccionan etapas.

```bash
python3 -m pipeline run --config <cfg> --scene <escena> [--stages sfm,dense] [--from-stage dense] [--force]
python3 -m pipeline validate --config <cfg> --scene <escena>
python3 -m pipeline report          # consolida todos los metrics.json en un CSV comparativo
```

## Métricas

`metrics/metrics.json` incluye por experimento: nº de frames por fuente,
imágenes registradas vs. totales, nº de puntos sparse/densos, longitud media de
track, error medio de reproyección (px), vértices/caras de la malla, tamaños de
archivos y tiempo de pared por etapa. `python3 -m pipeline report` genera
`outputs/experiments_summary.csv` para comparar experimentos (ideal para las
tablas de la tesis).

## Documentación

- [docs/khipu-workflow.md](docs/khipu-workflow.md) — flujo completo en Khipu (SLURM, particiones, shards de GPU, transferencia de archivos).
- [docs/configuration.md](docs/configuration.md) — referencia de cada parámetro + recetas (2 fps a 1080p, multi-fuente, loop detection, comparación OpenMVS vs COLMAP denso).
- [containers/README.md](containers/README.md) — construcción y mantenimiento de la imagen Apptainer.

## Requisitos

- En Khipu: solo Apptainer y SLURM (ya instalados). Nada que instalar como usuario.
- Local (opcional, para probar sin cluster): `ffmpeg`, `colmap`, OpenMVS y `python3` + `pyyaml` en el PATH.
