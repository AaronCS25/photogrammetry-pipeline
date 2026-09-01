# Contenedor Apptainer

La imagen `photogrammetry.sif` empaqueta **FFmpeg + COLMAP 3.11.1 (CUDA) +
OpenMVS 2.3.0 (CUDA) + Python/PyYAML** sobre CUDA 12.6 / Ubuntu 22.04. Se
compila para las tres GPUs de Khipu (T4, A100, RTX A6000), así que la misma
imagen sirve en cualquier nodo GPU.

La construcción está partida en **dos etapas** para no recompilar todo cuando
solo cambia la última pieza:

1. `base.def` → `base.sif`: dependencias + Ceres + COLMAP (~1-1.5 h). Ya es
   utilizable por sí sola para frames/sfm/undistort y el denso de COLMAP.
2. `photogrammetry.def` → `photogrammetry.sif`: `base.sif` + OpenMVS (~15-30 min).

## Construcción (una sola vez, en el nodo maestro)

Solo el nodo maestro de Khipu tiene internet, y `apptainer build` necesita
descargar la imagen base y los repositorios. Usar **tmux** (no `nohup`): el
`%post` de Apptainer muere con SIGHUP si se cierra la sesión SSH.

```bash
tmux new -s build              # sesión persistente; Ctrl+B luego D para salir
cd ~/photogrammetry-pipeline/containers
apptainer build base.sif base.def 2>&1 | tee build-base.log
apptainer build photogrammetry.sif photogrammetry.def 2>&1 | tee build.log
# volver luego con: tmux attach -t build
```

Genera imágenes de varios GB. Al terminar, verificar:

```bash
apptainer test photogrammetry.sif
apptainer exec photogrammetry.sif colmap -h | head -5
srun -p debug-gpu --gres=shard:1 --mem=4G \
     apptainer exec --nv photogrammetry.sif nvidia-smi
```

## Mantenimiento

- **Caché**: Apptainer cachea blobs en `$HOME/.apptainer/cache`. Tras el build,
  liberar espacio con `apptainer cache clean`.
- **Actualizar versiones**: editar los tags (`3.11.1`, `2.2.0` en `base.def`;
  `v2.3.0` en `photogrammetry.def`) y reconstruir solo la etapa afectada. Si
  OpenMVS `v2.3.0` fallara al compilar contra una CUDA más nueva, probar con la
  rama `master` (`--branch v2.3.0` → quitar el flag): solo cuesta el rebuild de
  la etapa 2.
- **`base.sif` no se borra**: es la caché que permite iterar OpenMVS en minutos.
- **La imagen NO se versiona en git** (está en `.gitignore`): cada integrante
  la construye a partir del `.def`, que es la fuente de verdad reproducible.

## Notas

- `--nv` es obligatorio al ejecutar en nodos GPU; sin él CUDA no es visible.
- Los binarios de OpenMVS quedan en `/usr/local/bin/OpenMVS` (ya está en el
  `PATH` del contenedor).
- COLMAP se compila **sin GUI** (innecesaria en el cluster). Para inspeccionar
  modelos usar `colmap model_analyzer` o descargar los resultados y abrirlos
  localmente (CloudCompare / MeshLab / COLMAP GUI de escritorio).
