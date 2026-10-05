# Referencia de configuración

La configuración final de un run es el **merge profundo** de
`configs/default.yaml` con el YAML de experimento pasado en `--config`:
los diccionarios se combinan clave a clave; escalares y listas del experimento
**reemplazan** al default. La config exacta usada queda guardada en
`outputs/<escena>/<exp>/config_resolved.yaml`.

Regla general: cada herramienta tiene sus parámetros más comunes expuestos con
nombre propio, y un `extra_args` para pasar **cualquier** flag adicional sin
tocar el código:

- COLMAP / OpenMVS: mapeo `{nombre-de-flag: valor}` → `--nombre-de-flag valor`
  (los booleanos se convierten a `1`/`0`).
- FFmpeg: lista de strings que se insertan tal cual antes del archivo de salida.

## `experiment`

| Clave | Descripción |
|---|---|
| `name` | Nombre del experimento; define la carpeta `outputs/<escena>/<name>/`. Un nombre nuevo = run desde cero sin tocar los anteriores. |
| `notes` | Texto libre; se propaga a `metrics.json` (útil para las tablas de la tesis). |

## `frames` (FFmpeg)

| Clave | Default | Descripción |
|---|---|---|
| `fps` | `2` | Frames por segundo extraídos (`-vf fps=N`). Acepta decimales (`0.5` = 1 frame cada 2 s). |
| `resize` | `null` | `null` (original), `{long_edge: 1920}` (recomendado: mantiene aspecto) o `{width: W, height: H}` (encaja dentro de W×H manteniendo aspecto). |
| `format` | `jpg` | `jpg` o `png` (png sin pérdida, ~10× más pesado y más lento en COLMAP). |
| `jpg_quality` | `2` | `-qscale:v`: 1 (mejor) a 31 (peor). 2-3 es prácticamente sin pérdida visible. |
| `start` / `end` | `null` | Recorte temporal del video (`"00:00:05"`), útil para despegue/aterrizaje del dron. |
| `extra_args` | `[]` | Flags extra de ffmpeg, ej. `["-vf", "..."]` avanzados. |

### `sources` — overrides por fuente

Cualquier clave de `frames` puede sobreescribirse para una subcarpeta concreta
de la escena:

```yaml
sources:
  drone: {fps: 2, start: "00:00:08"}
  phone: {fps: 3, resize: {long_edge: 1600}}
```

## `telemetry`

`enabled: true|false`. Parsea los `.srt` de DJI (formato moderno
`[latitude: ...]` y antiguo `GPS(...)`) a CSV en `telemetry/<fuente>/`. No
interviene aún en la reconstrucción (reservado para geo-registro futuro).

## `masking` — enmascaramiento semántico (opcional, OFF por defecto)

Genera una máscara por frame (0 = ignorar, 255 = usar) con un modelo de
segmentación, y la aplica en COLMAP (`--ImageReader.mask_path`, no se extraen
features sobre obstáculos) y opcionalmente en OpenMVS
(`--mask-path`/`--ignore-mask-label`, no se densifican esos píxeles).

| Clave | Default | Descripción |
|---|---|---|
| `enabled` | `false` | Con `false` el pipeline es idéntico al flujo sin máscaras (ni requiere el contenedor de segmentación). |
| `backend` | `segformer` | Un nombre **o una lista** de backends encadenados (`[segformer, sam3, manual]`): cada uno genera su máscara y se fusionan (se ignora todo píxel que cualquiera ignore). Módulos en `pipeline/mask_backends/`; añadir otro modelo = un módulo con el mismo contrato + registrarlo. |
| `backends.<nombre>.classes` / `.dilate_px` | (globales) | Cada backend puede sobreescribir `classes` y `dilate_px` en su propio bloque; si no, hereda los globales. Así cada pieza del lego se configura sola sin afectar a las demás. |
| `backends.sam3.prompts` | `[power line, electric cable, wire, utility pole]` | SAM 3 segmenta por texto libre (sustantivos simples). Fuerte en estructuras finas — el remedio para cables. Pesos *gated* en HF (`facebook/sam3`): aceptar licencia y descargar con token en el maestro. |
| `backends.sam3.score_threshold` | `0.5` | Confianza mínima por instancia detectada. |
| `backends.sam3.tiles` | `null` | `[columnas, filas]`: pasada extra por teselas que se **suma** a la de la imagen completa. SAM 3 trabaja a ~1008 px: en fotos grandes (dron 36 MP) los cables quedan por debajo de 1 px y no se detectan. `tile_overlap` (0.15) es el solape; `tile_prompts` limita qué prompts se buscan en las teselas (`null` = todos). Coste: una inferencia extra por tesela. |
| `backends.manual.dir` | `mask_overrides` | Carpeta (relativa a `datasets/raw/<escena>/`) con PNGs pintados a mano (`<imagen>.png`, negro = ignorar), opcionalmente en subcarpeta por fuente. Encadenado con los automáticos, las ediciones **sobreviven a regeneraciones**. |
| `classes` | dinámicos + `sky` | Qué enmascarar (vocabulario Cityscapes en segformer: `person rider car truck bus train motorcycle bicycle vegetation terrain sky pole traffic_light traffic_sign building road ...`). |
| `dilate_px` | `15` | Margen alrededor de cada objeto. También absorbe el desplazamiento de la undistorsión cuando `apply_to_dense: true`. |
| `apply_to_dense` | `false` | Pasa las máscaras a `DensifyPointCloud`. Clave para que los árboles (estáticos, densificables) no entren a la nube. |
| `backends.segformer.model_id` | SegFormer-B5 Cityscapes | Cualquier checkpoint SegFormer de HF (B2 = más rápido, B5 = mejor). |

Notas de honestidad: enmascarar `vegetation` elimina árboles pero **deja
huecos** donde tapaban fachada (solo más cobertura los rellena); los **cables**
no son segmentables (2 px de grosor) — se mitigan con `dilate_px` sobre los
postes y `remove-spurious` en la malla. Imágenes con >80% enmascarado se
reportan en `metrics/masks_info.json`.

Requisitos: `containers/segmentation.sif` + pesos descargados (ver
`containers/README.md`). La etapa es autónoma:

```bash
# Solo generar/inspeccionar máscaras (sin reconstruir nada):
apptainer exec --nv containers/segmentation.sif \
    python3 -m pipeline run --config <cfg> --scene <escena> --stages masks
```

En SLURM no hay que hacer nada especial: `pipeline.sbatch` detecta
`masking.enabled` y ejecuta la fase de máscaras en el contenedor de
segmentación antes del run principal.

## `colmap`

| Clave | Default | Descripción |
|---|---|---|
| `camera_model` | `OPENCV` | Modelo de cámara. `OPENCV` funciona bien para dron y teléfono. `OPENCV_FISHEYE` para action cams. |
| `single_camera_per_source` | `true` | Una cámara compartida por subcarpeta (correcto cuando todos los videos de una fuente vienen del mismo dispositivo con zoom fijo). |
| `feature_extractor.max_image_size` | `2400` | Reescalado interno para SIFT; subirlo mejora detalle y cuesta tiempo/VRAM. |
| `feature_extractor.max_num_features` | `8192` | Máximo de features por imagen. |
| `matcher.methods` | `[sequential]` | Se ejecutan en orden y los matches se **acumulan**: `[sequential, vocab_tree]` es la receta multi-fuente. |
| `matcher.sequential_overlap` | `10` | Vecinos temporales a matchear. Subir si el dron se mueve lento o hay pasadas superpuestas. |
| `matcher.loop_detection` | `false` | Cierre de bucles en matching secuencial (requiere `vocab_tree_path`). Recomendado en órbitas alrededor de un objeto. |
| `matcher.vocab_tree_path` | `null` | Ruta al `.bin` de vocabulario (descarga: `https://demuc.de/colmap/`). |
| `mapper.extra_args` | `{}` | Ej.: `{Mapper.ba_global_function_tolerance: 1e-6}` acelera el BA global. |
| `undistort.max_image_size` | `-1` | Límite de tamaño de las imágenes sin distorsión (entrada del denso). |

Ejemplo de `extra_args`:

```yaml
colmap:
  feature_extractor:
    extra_args:
      SiftExtraction.estimate_affine_shape: 1
      SiftExtraction.domain_size_pooling: 1
```

## `georef` — georreferenciación y región de interés (opcional, OFF por defecto)

Etapa entre `sfm` y `undistort`. Sin ella el modelo queda en el marco
arbitrario de COLMAP; con ella, en **metros reales, eje Z hacia arriba y origen
en el centro de la escena**, y opcionalmente recortado a una región de interés.

| Clave | Default | Descripción |
|---|---|---|
| `enabled` | `false` | Con `false` el pipeline es idéntico al flujo sin georef. |
| `alignment_type` | `enu` | `enu`: `colmap model_aligner` con el GPS del EXIF (leído de la base de datos) → marco métrico. `plane`: sin GPS, endereza con el plano principal (no métrico). |
| `reference_file` | `null` | CSV con posiciones para imágenes **sin GPS en el EXIF** (p. ej. el `indice.csv` de un export de Street View): columnas `file`\|`name`, `lat`, `lon` y `alt` opcional (default 0). Relativo a la carpeta de la escena o absoluto. Las filas se emparejan por `fuente/archivo` o por nombre de archivo único. `null` = GPS del EXIF. |
| `max_error_m` | `3.0` | Error máximo del ajuste robusto contra el GPS (el GPS de consumo tiene 2–5 m). |
| `fallback_plane` | `true` | Si no hay GPS suficiente, usar `plane` con aviso en vez de fallar. |
| `center` / `center_source` | `true` / `null` | Traslada el origen al centro de la escena (mediana XY de las cámaras; `center_source: drone` usa solo esa fuente — recomendable si el teléfono solo cubre un lado). `z = 0` queda cerca del suelo. |
| `roi.enabled` | `false` | Calcula una caja alrededor del centro y se la pasa a OpenMVS: solo se densifica y malla dentro. Requiere `enu` + `center`. |
| `roi.radius_m` | `null` | Semilado de la caja. `null` = distancia horizontal mediana cámara→centro × `radius_factor`. El valor usado queda en `metrics/georef_info.json` para ajustarlo. |
| `roi.below_m` / `roi.height_m` | `3.0` / `null` | Límites verticales (por defecto hasta la cámara más alta). |

En escenas mixtas basta con que **una** fuente tenga GPS (p. ej. el dron):
alinea todo el modelo, incluidas las fotos sin EXIF. Para iterar la ROI sin
repetir el sparse: `--from-stage georef --force` (borrando antes `mvs/` y
`colmap/undistorted/`, porque los depth-maps previos están en otro marco).

## `dense`

| Clave | Default | Descripción |
|---|---|---|
| `backend` | `openmvs` | `openmvs` (recomendado) o `colmap` (PatchMatch + fusión, para comparar). |
| `colmap.patch_match.*` | — | Solo con backend `colmap`. `geom_consistency` mejora calidad (2× tiempo). |
| `colmap.mesher` | `none` | `poisson` o `delaunay` sobre `fused.ply` (solo backend `colmap`). |

## `openmvs`

| Clave | Default | Descripción |
|---|---|---|
| `densify.resolution_level` | `1` | 0 = resolución completa (mucha VRAM), 1 = mitad, 2 = cuarto. Primer mando de ajuste calidad↔recursos. |
| `densify.number_views` | `0` | Vistas usadas por cálculo de profundidad (0 = todas). |
| `mesh.enabled` | `true` | Genera `scene_mesh.ply` con ReconstructMesh. |
| `mesh.decimate` | `1.0` | Factor de decimación (0.5 = mitad de caras). |
| `refine.enabled` | `true` | RefineMesh (mejora detalle; costoso). Desactivar en pruebas. |
| `refine.scales` | `2` | Nº de escalas del refinamiento. |
| `texture.enabled` | `true` | TextureMesh sobre la malla (refinada si existe). |
| `texture.export_type` | `obj` | `obj` \| `ply` \| `glb`. |
| `*.extra_args` | `{}` | Cualquier flag de la herramienta, ej. `{number-views-fuse: 3}`. |

## `runtime`

`gpu: true|false`. Con `false`: SIFT/matching de COLMAP en CPU y OpenMVS con
`--cuda-device -2`; el backend denso de COLMAP no funciona sin GPU.

## Recetas

**2 fps a "1080p" (lado mayor 1920)** — `configs/experiments/drone_2fps_1080p.yaml`.

**Encajar exactamente en 1080×720:**

```yaml
frames:
  resize: {width: 1080, height: 720}
```

**Dron + teléfono** — `configs/experiments/multisource_drone_phone.yaml`
(clave: `methods: [sequential, vocab_tree]`).

**Órbita alrededor de un edificio (cierre de bucle):**

```yaml
colmap:
  matcher:
    methods: [sequential]
    sequential_overlap: 20
    loop_detection: true
    vocab_tree_path: resources/vocab_tree_flickr100K_words256K.bin
```

**Máxima calidad (GPU grande, horas de cómputo):**

```yaml
frames:
  fps: 3
  resize: null            # resolución original 4K
colmap:
  feature_extractor: {max_image_size: 3200, max_num_features: 16384}
openmvs:
  densify: {resolution_level: 0}
  refine: {scales: 3}
```

**Comparación OpenMVS vs COLMAP denso:** correr el mismo YAML dos veces
cambiando solo `experiment.name` y `dense.backend`; luego
`python3 -m pipeline report` deja ambos en `experiments_summary.csv`.
