# Plan: COLMAP 4.x con descriptores aprendidos (ALIKED + LightGlue)

Documento de arranque para implementar esto en una rama aparte
(`feature/colmap4-aliked`). Contiene todo lo que hay que saber para empezar en
un chat nuevo sin releer el historial.

## 1. Objetivo

Añadir al pipeline, como pieza opcional ("lego": apagada por defecto, los
experimentos actuales no cambian), la posibilidad de sustituir SIFT por los
descriptores aprendidos que COLMAP 4.x trae nativos:

- **ALIKED** como extractor de features (vía ONNX Runtime, CPU o GPU).
- **LightGlue** como emparejador, tanto para ALIKED como para SIFT.

Motivación: los casos donde SIFT fragmenta el registro — Street View con
panoramas cada ~10 m y de fechas distintas (`sv_manzana_a/b`, `sv_7c34`), la
escalera interior de UTEC (20 islas), la fusión dron + teléfono — y poses más
precisas en general. No cambia la densificación (OpenMVS usa fotoconsistencia,
no features).

Resultado esperado al final: ablación en el mismo dataset con
`sift`, `sift + lightglue` y `aliked + lightglue`: imágenes registradas, número
de modelos, error de reproyección, tiempo.

## 2. Contexto del proyecto (lo imprescindible)

- Repo `photogrammetry-pipeline/` (tesis PFC-II, UTEC). Pipeline formal
  fotos/video → COLMAP → OpenMVS, configurado por YAML
  (`configs/default.yaml` + `configs/experiments/*.yaml`), etapas
  `frames, telemetry, masks, sfm, georef, undistort, dense, mesh, clean, texture, metrics`
  (`pipeline/__main__.py`, `STAGE_ORDER`). Ver `docs/configuration.md` y
  `docs/khipu-workflow.md`.
- Corre en el clúster **Khipu** (SLURM) dentro de contenedores Apptainer:
  `containers/base.def → base.sif` (CUDA 12.6 / Ubuntu 22.04, Ceres 2.2.0,
  COLMAP **3.11.1**, `CUDA_ARCHS="75;80;86"` para T4 / A100 / RTX A6000) y
  `containers/photogrammetry.def → photogrammetry.sif` (`base.sif` + OpenMVS
  2.3.0 + numpy + PIL). Builds en el nodo maestro, dentro de `tmux`
  (`apptainer build x.sif x.def 2>&1 | tee build.log`); solo el maestro tiene
  internet. `containers/README.md` documenta el procedimiento.
- `slurm/pipeline.sbatch` acepta `CONTAINER=containers/<otro>.sif` por
  variable de entorno (y `MASKS_CONTAINER` para el de segmentación), así que un
  contenedor nuevo se prueba sin tocar código:
  `CONTAINER=containers/photogrammetry4.sif ./slurm/submit.sh <cfg> <escena>`.
- Reglas de trabajo con el usuario: **el asistente nunca hace commits** (deja
  los cambios en staging y sugiere el mensaje en conventional commits con el
  trailer `Co-Authored-By`); el usuario hace `git pull` y ejecuta TODO en Khipu
  a mano y pega las salidas; nunca poner tokens en YAML ni en el repo.
- COLMAP se invoca desde `pipeline/sfm.py` (`feature_extractor`, matcher por
  `colmap.matcher.methods` = sequential / exhaustive / spatial / vocab_tree,
  `mapper`); los flags extra llegan por `extra_args` de cada bloque
  (`extra_args_to_cli` en `pipeline/executor.py`). Los intrínsecos fijos se
  pasan con `ImageReader.camera_params`. Las máscaras (`masking.enabled`) van
  por `ImageReader.mask_path`.
- Lección pagada: `SiftExtraction.estimate_affine_shape` / `domain_size_pooling`
  corren en CPU con un hilo por núcleo del nodo → fijar
  `SiftExtraction.num_threads: 8` o hay OOM.

## 3. Hechos sobre COLMAP 4.x (verificar al empezar)

- COLMAP 4.0 (marzo 2026) añadió ALIKED y LightGlue nativos mediante ONNX
  Runtime; hay vocab trees preentrenados para ALIKED. Fuentes:
  https://radiancefields.com/colmap-4.0-introduces-major-performance-and-infrastructure-updates
  y el FAQ oficial (`doc/faq.rst`), que indica compilar con `-DONNX_ENABLED=ON`.
- Desde 3.12 el modelo sparse incluye `rigs.bin` y `frames.bin` además de
  `cameras/images/points3D`. OpenMVS 2.3.0 (`InterfaceCOLMAP`) debería
  ignorarlos; **comprobarlo** con una escena pequeña antes de dar nada por bueno.
- Los nombres exactos de los flags de extracción/matching (tipo de feature,
  tipo de matcher, rutas de los modelos ONNX, uso de GPU en ONNX) NO están
  verificados: leer `colmap feature_extractor -h`, `colmap exhaustive_matcher -h`
  y la documentación 4.x del repo (`doc/tutorial.rst`, `doc/faq.rst`,
  CHANGELOG) antes de escribir código.
- Preguntas abiertas: ¿los pesos ONNX de ALIKED/LightGlue se descargan en
  tiempo de ejecución (los nodos NO tienen internet → descargarlos en el
  maestro y fijar la ruta/caché) o van en el build? ¿ONNX Runtime con proveedor
  CUDA viene con el build o hay que instalarlo aparte? ¿Qué versión mínima de
  CMake, compilador y Ceres pide COLMAP 4.x (Ubuntu 22.04 trae CMake 3.22 y
  GCC 11)? ¿Siguen existiendo `model_aligner --alignment_type enu/plane`,
  `--ref_images_path`, `image_undistorter`, `model_converter` con los mismos
  flags (los usa `pipeline/georef.py` y `sfm.py`)?

## 4. Pasos

1. **Rama** `feature/colmap4-aliked` desde `main`.
2. **Contenedor nuevo sin tocar los actuales**: `containers/base4.def`
   (copia de `base.def` con COLMAP 4.x, `-DONNX_ENABLED=ON`, ONNX Runtime con
   CUDA, Ceres/CMake más nuevos si hace falta) → `base4.sif`; y
   `containers/photogrammetry4.def` (OpenMVS sobre `base4.sif`) →
   `photogrammetry4.sif`. Build en tmux en el maestro (~2 h). `%test` debe
   imprimir la versión de COLMAP y comprobar que `feature_extractor -h` lista
   ALIKED.
3. **Verificar compatibilidad** con el pipeline actual (sin cambios de código,
   solo `CONTAINER=containers/photogrammetry4.sif`) sobre una escena pequeña:
   `sv_7c34` con `configs/experiments/streetview_single.yaml` (36 fotos,
   minutos). Debe pasar sfm, georef (model_aligner), undistort, dense, mesh,
   clean, texture. Si OpenMVS tropieza con `rigs.bin`/`frames.bin`, resolverlo
   aquí (p. ej. exportar el modelo sin esos archivos antes de `InterfaceCOLMAP`).
4. **Claves nuevas en YAML** (`configs/default.yaml`, default = comportamiento
   actual):
   - `colmap.feature_extractor.type: sift | aliked`
   - `colmap.matcher.type: sift | lightglue`
   - lo que haga falta para rutas de modelos ONNX / GPU.
   `pipeline/sfm.py` traduce a flags; `validate` lo muestra; `metrics.json`
   guarda el tipo usado (procedencia). Documentar en `docs/configuration.md`.
   Si se usa `aliked`, no aplicar los flags `SiftExtraction.*` (afín, DSP,
   num_threads) y avisar si el YAML los trae.
5. **Experimentos de ablación**, mismos datos, solo cambia descriptor/matcher:
   `streetview_single_v1` (sift, ya hecho) vs `streetview_single_lg` (sift +
   lightglue) vs `streetview_single_aliked` (aliked + lightglue); después
   `manzana_03` con `manzana_phone.yaml` (466 fotos) y, si vale la pena,
   `utec_escaleras`. Comparar en `metrics.json`: registradas, modelos, error
   de reproyección, tiempos.
6. Si ALIKED gana de forma clara, decidir si `photogrammetry4.sif` pasa a ser
   el contenedor por defecto (actualizar `containers/README.md`) o se mantiene
   como opción.

## 5. Criterio de éxito

- `main` sigue intacto: los YAML actuales corren igual con el contenedor viejo.
- Con el contenedor nuevo y `type: sift`, los resultados de `sv_7c34` son
  equivalentes a los de COLMAP 3.11.1.
- Con `aliked + lightglue`, el pipeline completo termina y `metrics.json`
  documenta el descriptor; la comparación queda lista para la tesis.
