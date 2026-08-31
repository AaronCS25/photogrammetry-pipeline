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
