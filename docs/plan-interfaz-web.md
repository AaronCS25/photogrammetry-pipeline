# Plan: interfaz web local para operar el pipeline en Khipu

Estado: **fases 1 y 2 implementadas** (2026-10-06) en `web/` (ver
`web/README.md`); fases 3-4 pendientes. La fase 2 tiene tests con Khipu
simulado; falta su primera prueba real (subida + validate + submit). Este
documento es el brief para quien implemente las siguientes; está escrito para
alguien que no ha visto las conversaciones previas.

Desviaciones y decisiones de la fase 2:
- Subida: en vez de `scp` por fuente, un tar generado en Node se envía por
  `ssh khipu "tar -x"` (una conexión por fuente). Da progreso exacto por bytes,
  no depende de rsync y es reanudable: antes se lista la escena remota y se
  omiten los archivos con el mismo nombre y tamaño. Verificación final: todos
  los archivos presentes con el mismo tamaño (más fuerte que `ls | wc -l`).
- Fotos con dimensiones mixtas (verticales) se suben automáticamente a una
  fuente aparte `<fuente>_<ancho>x<alto>/` (una cámara por fuente).
- `validate` corre en el maestro con `apptainer exec
  containers/photogrammetry.sif` (el conda del maestro no tiene PyYAML).
- Envío idempotente: recibo en `datasets/raw/<escena>/_ui/requests/<id>.json`
  escrito ANTES de `sbatch` y `--comment=studio:<id>`. Si se pierde la
  respuesta, «Comprobar envío» reintenta con el mismo id y el bridge busca el
  job por comentario (squeue) o por la cabecera del log; nunca relanza a
  ciegas. Ojo: Khipu no guarda comentarios en sacct (`AccountingStoreFlags`
  vacío).
- SBATCH_OPTS se derivan del formulario (GPU, shards, CPUs, RAM, horas) y son
  editables; solo se aceptan `--gres`, `--cpus-per-task`, `--mem`, `--time`,
  `--partition`, `--nodelist`, `--exclude`. SAM 3 fuerza A6000/A100.
- Relanzar: «Reanudar» (mismo YAML, etapas hechas se omiten) o «Repetir desde
  etapa» (`--from-stage X --force`), siempre con validate antes. «Nueva
  versión» clona la configuración (formulario o `config_resolved.yaml`) y puede
  reutilizar etapas: el bridge copia `outputs/<escena>/<origen>/` sin lo que
  rehacen las etapas siguientes y sin sus marcadores.
- Cancelar: `scancel` solo si `squeue --me -j <id>` dice que es un job
  `photogram` propio.

Desviaciones de la fase 1 respecto a este plan:
- SQLite con `node:sqlite` (nativo de Node 22, como barranco-studio) en vez de
  `better-sqlite3`: evita compilar un módulo nativo en Windows. Archivo
  `web/data/studio.db`. Tablas de fase 1: `settings`, `manzanas` (nombre
  libre), `scene_links` (vínculo manual escena→manzana), `snapshots` (último
  listado). Datasets/experimentos/jobs/eventos se añadirán en la fase 2.
- GIS: el archivo real es `barranco-streetview/data/lotes.geojson` (ya en
  WGS84). `web/scripts/build_manzanas.py` genera `web/public/gis/manzanas.geojson`
  y `lotes.geojson`.
- Puerto 4323 (barranco-studio usa 4322).
- Las escenas existentes no siguen la convención `mz_xxxxxx`; se vinculan a
  una manzana por `identificacion.json`/`manifest.json` del export de Street
  View, por el origen GPS del georef (≤ 30 m) o a mano desde el mapa.

## Problema

Hoy cada reconstrucción de una manzana son ~15 comandos manuales: copiar fotos
a Khipu con `scp`, escribir un YAML de experimento, `git push` + `git pull`,
`./slurm/submit.sh`, mirar `squeue`/`sacct`, leer `slurm-logs/`, `grep` de
métricas, `scp` de la malla. Para cubrir Barranco (decenas de manzanas, varias
versiones por manzana) eso no escala.

## Objetivo

Una app web **local** (laptop, `127.0.0.1`) que, con un mapa de Barranco y su
GIS de lotes, permita: elegir una **manzana**, subir sus imágenes (dron,
teléfono, video, export de Street View), definir la configuración (modo simple
o avanzado), lanzar el job en Khipu, seguir su estado y ver/descargar
resultados y errores. Minimalista, bien hecha, sin "cientos de comandos".

No objetivos (por ahora): multiusuario, autenticación, ejecutar nada fuera de
Khipu, visualizar mallas grandes dentro del navegador (se descargan y se abren
en Blender; `model-viewer` solo para vistas previas pequeñas si se quiere).

## Stack (decidido: repetir el de `sam3d-barranco/web`, "barranco-studio")

- Astro 7 + `@astrojs/node` (SSR), Node >= 22. Sin framework de UI pesado.
- Mapa: Leaflet 1.9 con fondo OpenStreetMap. GIS: GeoJSON de lotes ya
  existente en `barranco-streetview/data/lotes_wgs84.geojson` (4.926 lotes).
  La agrupación de lotes contiguos en manzanas la hace `barranco-streetview`
  (`gis.py`/`geometry.py`): **precomputar una vez** `manzanas.geojson`
  (polígono + id `MZ-BAR-<hash>` + lotes) con un script Python y servirlo
  estático; la app no hace geometría en tiempo real.
- Acceso a Khipu: `child_process` con los binarios del sistema `ssh` y `scp`
  (OpenSSH de Windows) usando el alias `khipu` de `~/.ssh/config`, igual que
  `sam3d-barranco/web/src/lib/connector.mjs` (reutilizar/copiar ese módulo).
  La app nunca guarda credenciales. Solo el nodo maestro tiene internet; los
  comandos de la app corren en el maestro (ligeros: scp, sbatch, squeue, cat).
- Estado local: SQLite (`better-sqlite3`) en `web/data/studio.db`: manzanas,
  datasets, experimentos, jobs, eventos. Khipu es la fuente de verdad para
  archivos; la BD local es índice/caché.
- Ubicación: `photogrammetry-pipeline/web/`. Se versiona con el pipeline
  (genera sus YAML y usa su CLI). El usuario hace los commits.

## Convenciones del pipeline que la app debe respetar

- Escena = `datasets/raw/<escena>/` en Khipu; cada subcarpeta es una fuente
  (`drone/`, `phone/`, `video/`...). Fotos sueltas se copian preservando EXIF;
  videos se extraen con `frames.fps`. Carpetas reservadas: `mask_overrides/`
  (y la que indique `masking.backends.manual.dir`), prefijos `_` y `.`.
- Export de Street View (`barranco-streetview`) se sube **tal cual** como
  escena: `fachadas/` (fuente), `mascaras/` (overrides), `indice.csv`
  (`georef.reference_file`). Ver `configs/experiments/streetview_manzana.yaml`.
- Nombre de escena propuesto: id corto de la manzana (p. ej. `mz_7c34f4`), y
  experimento `<preset>_v<N>` → salidas en `outputs/<escena>/<exp>/`.
- Lanzamiento: `SBATCH_OPTS="--gres=shard:rtxa6000:8 --cpus-per-task=16
  --mem=64G --time=0-6:00:00" ./slurm/submit.sh <yaml> <escena> [--from-stage X
  --force | --stages a,b]`. Imprime `Submitted batch job <id>`.
- Límites QOS `a-investigacion1`: **130G de RAM sumando todos los jobs del
  usuario** (si se excede queda PD `QOSMaxMemoryPerUser`), 3 días. SAM 3 exige
  A6000/A100 (`shard:rtxa6000:N` / `shard:a100:N`), no T4.
- Estado: `squeue --me` (PD/R + razón) y `sacct -j <id>
  --format=JobID,State,Elapsed,ExitCode` (COMPLETED / FAILED / OUT_OF_MEMORY /
  TIMEOUT / CANCELLED). Log: `slurm-logs/photogram-<id>.out`; las líneas del
  pipeline empiezan por `[etapa]`; errores: `[pipeline] ERROR: ...`,
  `ADVERTENCIA`, `AVISO`, `Traceback`.
- Resultados: `outputs/<escena>/<exp>/metrics/metrics.json`
  (`sparse.registered_images`, `registration_ratio`, tiempos, georef,
  masking), `metrics/georef_info.json`, `logs/*.log`, `mvs/scene_texture.obj`
  + `.mtl` + `scene_texture_material_*_map_Kd.jpg` (descargable), máscaras en
  `masks/<fuente>/<img>.png` (vista previa = overlay rojo sobre
  `frames/<fuente>/<img>`; hoy se genera con un script dentro de
  `containers/segmentation.sif`, porque el conda del maestro no tiene PIL).
- Reanudación: marcadores `.stages/<etapa>.done`; al clonar un experimento
  para iterar (p. ej. cambiar ROI) hay que borrar `mvs/`, `colmap/undistorted/`
  y `metrics/metrics.json`. Etapas: frames, telemetry, masks, sfm, georef,
  undistort, dense, mesh, texture, metrics.
- `python3 -m pipeline validate --config <yaml> --scene <escena>` es la
  puerta antes de lanzar: imprime fuentes, cadenas de máscaras, georef, y
  `✔ Configuración válida.` o `ERROR de configuración`.
- Referencia de parámetros: `docs/configuration.md`. Presets existentes en
  `configs/experiments/`: `hornero_drone_geo_tiles.yaml` (dron solo, georef +
  ROI + SAM 3 mosaico), `hornero_drone_clean.yaml` (+ filtrado de cables),
  `barranco_full_masked_sam3.yaml` (dron + teléfono), `streetview_manzana.yaml`.

## Modelo de datos

- **Manzana**: id GIS (`MZ-BAR-…`), nombre libre, polígono, centro lat/lon.
- **Dataset** (captura): manzana + lista de fuentes, cada una con tipo
  (`drone_photos` | `phone_photos` | `video` | `streetview_export` | `other`),
  carpeta local de origen, nº de archivos, tamaño, fecha, estado de subida
  (pendiente / subiendo / subido / verificado) y ruta en Khipu. Un dataset =
  una escena en Khipu. Varias capturas de la misma manzana = varios datasets.
- **Experimento**: dataset + configuración (YAML completo generado) + preset +
  notas + versión. El YAML se guarda localmente y se sube a Khipu a
  `datasets/raw/<escena>/_ui/<exp>.yaml` (carpeta reservada por el prefijo
  `_`; no toca `configs/` del repo, así no hay que commitear YAMLs).
- **Job**: experimento + slurm id + SBATCH_OPTS + estado + timestamps + nodo +
  último resumen del log + razón de fallo. Un experimento puede tener varios
  jobs (relanzamientos, `--from-stage`).
- **Resultado**: métricas parseadas (registradas/total, modelos, tiempos por
  etapa, georef, % enmascarado), lista de artefactos con tamaño y estado de
  descarga local.

## Pantallas (minimalistas)

1. **Mapa** (inicio): Barranco con manzanas; color por estado (sin datos /
   con datos / en cola / corriendo / listo / fallido). Clic → panel de la
   manzana. Buscador por nombre o id. Capa de lotes opcional.
2. **Manzana**: nombre, datasets (con fuentes), experimentos y sus jobs.
   Botones: "Nueva captura", "Nuevo experimento", "Relanzar".
3. **Nueva captura**: elegir carpetas locales por fuente (campo de ruta o
   diálogo), validación local (conteo, extensiones, dimensiones homogéneas
   por fuente, EXIF GPS presente o ausente, aviso de fotos verticales),
   "Subir a Khipu" con progreso (scp por fuente) y verificación
   (`ls | wc -l` remoto = local).
4. **Nuevo experimento**: modo **simple** = preset (Dron / Teléfono /
   Dron+Teléfono / Video / Street View) + conmutadores: máscaras
   (segformer / sam3 / manual, clases y prompts editables), georef (sí/no,
   `radius_m`, `height_m`), calidad (rápida / normal / máxima →
   resolution_level, refine, max_image_size), recursos (tipo de GPU, RAM,
   tiempo). Modo **avanzado** = editor del YAML resultante. Siempre: botón
   "Validar" (ejecuta `pipeline validate` por ssh y muestra la salida) y
   "Lanzar" (deshabilitado hasta validar). Aviso si la RAM pedida + jobs
   activos > 130G.
5. **Job**: estado en vivo (sondeo cada 5 min, o manual), etapas completadas
   (`.stages/*.done`), cola del log filtrada (`[etapa]`, avisos, errores),
   log completo bajo demanda, botón cancelar (`scancel`).
6. **Resultados**: tarjeta de métricas, georef, % máscaras, previews de
   máscaras (generadas en Khipu bajo demanda y traídas por scp, reducidas),
   botón "Descargar malla" (scp a
   `~/Downloads/barranco_experiments/<escena>__<exp>/`) y "Abrir carpeta".
   Comparación lado a lado de dos experimentos de la misma manzana (tabla de
   métricas).

## Flujos

- Lanzar: generar YAML → `scp` del YAML → `validate` por ssh → `submit.sh` →
  guardar slurm id → sondeo. Si `masking.enabled` y sam3 está en los backends,
  forzar gres A6000/A100.
- Sondeo: mientras haya jobs no terminales, cada 5 min `squeue --me` +
  `sacct` de los ids propios + `tail -n 40` del log; al pasar a estado
  terminal, traer `metrics.json` y `georef_info.json` y marcar resultado. Un
  solo ssh por tick que ejecute un script Python en el maestro y devuelva
  JSON (patrón de `connector.mjs`: `ssh khipu python3 -`).
- Relanzar: clonar experimento (opcionalmente `--from-stage` con la limpieza
  de carpetas que corresponda, hecha por la app en Khipu).

## Cambios pequeños en el pipeline que ayudan (opcionales, "lego")

- `python3 -m pipeline status --scene X --experiment Y --json`: etapas hechas,
  métricas si existen, últimos errores del log → un solo comando para la app.
- `python3 -m pipeline previews --scene X --experiment Y --max N`: genera los
  overlays de máscaras reducidos (hoy es un script suelto). Debe correr en el
  contenedor de segmentación o en `photogrammetry.sif` (tienen PIL).
- `submit.sh` ya imprime el job id; mantenerlo.

## Fases (MVP primero)

1. **Esqueleto + mapa + lectura**: app Astro, manzanas en el mapa, panel que
   lista escenas y experimentos existentes en Khipu (`ls outputs/`, métricas).
   Sin escribir nada en Khipu. Útil desde el día 1 para ver lo ya hecho.
2. **Captura + experimento + lanzar + sondeo**: subida con progreso,
   formulario simple/avanzado, validate, submit, estado, log filtrado,
   cancelar.
3. **Resultados**: métricas, previews de máscaras, descarga de malla,
   comparación entre experimentos.
4. Después: presets guardados por el usuario, Street View integrado (lanzar
   el export de `barranco-streetview` desde el mismo mapa), fusión con
   barranco-studio (SAM 3D) en un solo mapa.

## Riesgos y decisiones abiertas

- Subidas grandes (dron 36 MP: ~1 GB por manzana) por scp desde Windows:
  aceptable; mostrar progreso por archivo y permitir reanudar (saltar los
  que ya existen con el mismo tamaño).
- Windows: `ssh` y `scp` de OpenSSH están en PATH; rsync no. No depender de
  rsync.
- Nunca ejecutar `git` en Khipu ni en la laptop desde la app; el usuario
  gestiona los commits (regla del proyecto).
- Mantener el modo CLI intacto: la app solo compone comandos ya existentes.
- Abierto: si el formulario simple expone SBATCH_OPTS o los deriva del preset
  (propuesta: derivarlos, con "avanzado" para editarlos).
- Abierto: nombre de la app (propuesta: carpeta `web/`, nombre
  "photogrammetry-studio").
