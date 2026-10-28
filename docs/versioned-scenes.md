# Reconstrucciones versionadas por manzana

## Contrato

Una escena corresponde a una manzana. Los clips/fotos de esa escena comparten
una reconstrucción, pero nunca se mezclan automáticamente con otra escena.
Se conserva el layout `outputs/<escena>/<experimento>/` para el studio y los
reportes existentes. El flujo antiguo `run --config ...` continúa disponible.

La API nueva tiene tres capas:

```
CLI / API Python / bridge web
  -> service.py (solicitudes, versiones, exclusión mutua)
  -> planner.py (inventario y reutilización; solo lectura)
  -> scheduler/slurm.py (recibos y dependencias afterok)
  -> scene.sbatch (Apptainer)
  -> workflow.py (prepare, masks opcional, features, matching, mapping, surface opcional, publish)
       -> frames / masks / colmap / georef / mvs / clean / metrics
```

## Entorno: Conda existente frente a Apptainer

Las features históricas se calcularon con COLMAP 3.13.0 en Conda, sin máscaras,
2 fps, JPG q2, 3840x2160, SIMPLE_RADIAL, SIFT max_image_size=4000 y
max_num_features=8192. La API puede adoptarlas sin recalcular SIFT. La adopción
comprueba la BD, los BLOBs, nombres, cámaras, dimensiones y número de frames;
copia con SQLite backup (incluye datos WAL confirmados), nunca modifica las BD
originales. Los parámetros históricos son una declaración del job original,
no metadatos recuperables íntegramente desde SQLite: se registra esa limitación
de procedencia. El hash del original se registra en el momento de adopción.

El contenedor 3.11.1 **no se usa** con estas BD. La API versionada exige 3.13.0
y comprueba la versión en los workers. Apptainer no cambia las features por sí
solo: importa que coincidan formato, versión y receta. Cambiar de Conda al
contenedor no exige repetir SIFT. Las tablas de rigs, frames, frame_data,
cámaras, imágenes y pose_priors se remapean explícitamente en la consolidación.
Solo sensores de cámara son admitidos; un sensor desconocido causa error.

El worker usa `apptainer exec --cleanenv` para que el Conda/PYTHONPATH del host
no contamine la imagen. Solo propaga explícitamente presupuesto de hilos,
TMPDIR y metadatos de la asignación Slurm; añade `--nv` únicamente en fases GPU.

### Preparar el contenedor (operación separada, no automática)

Las recetas mantienen por defecto COLMAP 3.11.1 para los experimentos antiguos.
Para construir una imagen adicional, desde `containers/`, en el nodo de acceso:

```bash
apptainer build --build-arg COLMAP_VERSION=3.13.0 base313.sif base.def
apptainer build --build-arg BASE_IMAGE=base313.sif photogrammetry313.sif photogrammetry.def
cd ..
apptainer test containers/photogrammetry313.sif
apptainer exec containers/photogrammetry313.sif colmap -h
apptainer exec containers/photogrammetry313.sif python3 -m pipeline doctor --target textured
```

Fuera de Khipu:

```bash
docker build --build-arg COLMAP_VERSION=3.13.0 -t photogrammetry:3.13 -f containers/Dockerfile .
IMAGE=photogrammetry:3.13 ./containers/docker-run.sh python3 -m pipeline plan --scene 001
IMAGE=photogrammetry:3.13 ./containers/docker-run.sh python3 -m pipeline execute-scene --scene 001 --request-id local001
```

Construir no implica compatibilidad científica ya comprobada. Antes de usar
OpenMVS en producción se debe verificar una escena pequeña completa, incluyendo
el import de rigs/frames 3.13 mediante InterfaceCOLMAP 2.3.0, georef y texturas.
Las imágenes no se han construido como parte de las pruebas locales.

`execute-scene` ejecuta sin scheduler en un entorno ya preparado: usar Docker
en local o una asignación de cómputo, nunca el login de Khipu. Para máscaras con
cambio automático entre imágenes usar `submit` en Khipu; el modo síncrono inicial
requiere `masking.enabled: false`, como la receta Docker original.

## Configuración

- `configs/scenes/barranco.yaml`: catálogo de carpetas 001–025. Referencia las
  rutas actuales sin mover originales. Un ID registrado no garantiza material.
- `configs/scenes/<id>.yaml`: opcional, sustituye el registro de esa escena.
- `configs/profiles/barranco_sparse4k.yaml`: receta, calidad y recursos por fase.
- `pipeline` dentro del perfil: overrides de `configs/default.yaml` para georef,
  COLMAP denso, OpenMVS, limpieza, textura y métricas.

Los mapas `*_map.png`, `mapa*`, proxies LRF y archivos `.part` están excluidos.
Se puede extender `exclude` en un registro de escena. Cada archivo de captura
es un asset. Por defecto cada asset tiene su propio grupo de cámara; compartir
grupos requiere declararlo en `camera_groups: {drone: mi_camara}` y solo se
fusionan cámaras con modelo, dimensiones e intrínsecos iniciales iguales.

4K se refiere a la ingesta y SIFT. Undistortion usa tamaño original (`-1`),
pero el perfil explicita OpenMVS `resolution_level: 1` (media resolución) para
el denso; establecer 0 si se desea densificar a resolución completa.

## Uso

El intérprete desde el que se invoca la CLI necesita PyYAML. El `python3` de
Conda base puede no tenerlo; se puede usar el Python de `colmap_env` o un venv
ligero con las dependencias de `requirements.txt`. El envío no ejecuta cómputo.

```bash
python -m pipeline plan --scene 001 --target sparse
python -m pipeline submit --scene 001 --target sparse --request-id mz001-v1
python -m pipeline status --scene 001 --live

# Al añadir material: mismo comando con otra identidad de solicitud.
python -m pipeline submit --scene 001 --target sparse --mode update --request-id mz001-v2

# Reconstruir poses/puntos desde cero, reutilizando SIFT compatible:
python -m pipeline submit --scene 001 --mode rebuild --request-id mz001-rebuild

# Continuar a otros productos sin recalcular el sparse compatible:
python -m pipeline submit --scene 001 --target textured --request-id mz001-mesh-v1

# Reanudar exactamente la misma solicitud tras un fallo, cuando sus jobs estén inactivos:
python -m pipeline resume --scene 001 --request-id mz001-v1

# Recuperar un acuse perdido de sbatch sin volver a enviar:
python -m pipeline reconcile --scene 001 --request-id mz001-v1

# Manzanas independientes con dos cadenas activas como máximo:
python -m pipeline submit-batch --scenes 001,002,003 --batch-id campana-v1 --max-active-scenes 2
```

`plan` no escribe archivos ni envía jobs. Repetir `submit` con el mismo
request-id devuelve el recibo y no vuelve a enviar jobs. Tras un timeout de
sbatch, el recibo queda `uncertain`: reconciliar con squeue/sacct y su comentario
`scene:...:request:...:phase:...`; no se reenvía a ciegas. `resume` rechaza jobs
activos/desconocidos y solicitudes anteriores a otra versión publicada.

Si hay archivos inválidos, por defecto se bloquea el envío. `--allow-partial`
permite explícitamente trabajar con lo válido, registra las exclusiones y no
publica ese modelo como manzana completa. Las cinco descargas HTML pendientes
están documentadas en `barranco-pendientes.md`.

### API Python y bridge

```python
from pipeline import api

p = api.plan(scene="001", target="sparse")
r = api.submit(scene="001", request_id="mz001-v1")
s = api.status(scene="001", live=True)
```

El studio dispone de operaciones remotas `scene_plan`, `scene_submit`,
`scene_status`, `scene_resume` y rutas POST `scene/plan`, `scene/submit`,
`scene/status`, `scene/resume`. `request_id` es obligatorio en envíos web.
`PHOTOGRAM_PYTHON` permite seleccionar el intérprete ligero del host remoto.
La interfaz visual antigua sigue mostrando experimentos, métricas y jobs
`photogram`; la selección gráfica de estas operaciones nuevas puede añadirse
sin duplicar el motor. El relanzamiento legacy se rechaza para runs versionados.

## Reutilización e invalidación

| Cambio | Features | Matching/modelo |
|---|---|---|
| Nuevo archivo | Solo el nuevo asset | Nuevos pares y ampliación del modelo |
| Contenido modificado | Asset modificado | Reconstrucción desde cachés compatibles |
| FPS, resize, SIFT, cámara, máscaras | Nueva receta de caché | Reconstrucción |
| Hilos SIFT | Se reutilizan | Según los otros cambios |
| Matching | Se reutilizan | Se limpian/recalculan matches; se puede usar modelo previo como inicio |
| Mapper/BA | Se reutilizan | Nueva optimización |
| Denso/textura | Se reutilizan | Sparse idéntico se copia, luego se ejecuta el target solicitado |
| Archivo eliminado | Las cachés se conservan | Reconstrucción sin el asset eliminado |

El plan usa tamaño/mtime para detectar cambios sin leer GB en el login. El
worker calcula SHA-256 completo; si una caché tiene ese stamp pero otro hash,
falla en lugar de reutilizar descriptores incorrectos. No se fusionan assets
con contenido distinto a escondidas. Las ediciones manuales de máscaras se
incluyen en la firma por tamaño/mtime; los modelos/pesos externos deben estar
versionados y se debe cambiar la receta cuando se reemplacen.

Cada solicitud guarda una firma de los fuentes Python, el worker Slurm y la
configuración base. Si cambian entre envío y ejecución, el worker se detiene:
crear una solicitud nueva con el código actualizado. Esto conserva trazabilidad
sin invalidar automáticamente los descriptores por cambios ajenos a SIFT.

La temporalidad se expresa mediante pares dentro de cada clip, ordenados por
índice de frame (no image_id), y se complementa con matching global. El perfil
inicial usa exhaustive con un límite explícito de pares. Para escenas grandes
se puede usar `global: vocab_tree` con un árbol SIFT compatible; ese retrieval
puede volver a consultar imágenes antiguas sin repetir extracción.

## Persistencia y calidad

```
datasets/cache/<scene>/frames/<content-recipe>/
datasets/cache/<scene>/features/<asset-recipe>/database.db + record.json
datasets/_state/<scene>/requests/<id>.json + <id>.receipt.json
outputs/<scene>/<profile>_<id>/
  frames/ colmap/ metrics/ logs/ .stages/
  assets.json run.json quality.json config_resolved.yaml
```

Los frames de una versión enlazan a copias inmutables en la caché; las BD
modificables siempre se copian con SQLite backup, nunca con hardlinks. Mantener
las cachés mientras haya resultados que las referencien. No se implementa un
borrado automático de originales/cachés/versiones.

`latest.json` apunta solo a una versión completa que pasó los umbrales.
`last_result.json` también puede apuntar a un resultado parcial inspeccionable
y reutilizable. Un modelo desconectado, con clips sin registrar o entradas
excluidas no se etiqueta como completo. `quality.json` incluye componentes,
cobertura por clip, ratio de registro, reproyección y tracks. La nube visible
queda en `colmap/sparse.ply` y los modelos nativos en `colmap/sparse/`.

Los productos dense/mesh/textured están ligados a la versión sparse de ese run.
Los checkpoints de superficie permiten reanudar sin repetir etapas ya completas.
El refinado OpenMVS conserva el fallback documentado del pipeline original;
los logs indican si se utilizó malla sin refinar.

La etapa SRT antigua solo produce CSV. El flujo versionado añade una opción
explícita para interpolar referencias por frame, apagada por defecto:

```yaml
pipeline:
  georef:
    enabled: true
    srt: {enabled: true, time_shift_s: 0, max_gap_s: 1, min_images: 3}
```

Exige `abs_alt`, nunca sustituye con `rel_alt`; no extrapola ni cruza huecos
temporales grandes. El datum de altitud DJI y el desfase de muestreo deben
verificarse antes de usar el resultado como referencia métrica. También se
conservan las opciones EXIF, reference_file o GPX, sin mezclar referencias
implícitamente. Alinear entre manzanas continúa siendo una operación aparte.

## Recursos y pruebas

SIFT/matching/mapper tienen jobs y presupuestos de hilos separados. La receta
inicial usa CPU para features/mapper y GPU para matching y superficie. No existe spill de pirámides SIFT a
disco; los límites se controlan con hilos, RAM y concurrencia. `/local` se usa
como TMPDIR propio del job, no como almacenamiento persistente ni swap.

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s web/tests -p 'test_*.py' -v
# En web/, con dependencias Node instaladas:
npm test
```

Las pruebas locales usan fixtures SQLite 3.13, mocks de Slurm y del cómputo.
No ejecutan extracción, mapper, builds ni jobs. La prueba científica en Khipu
(001 y después una actualización con material adicional) queda separada y debe
realizarse antes de procesar el resto de manzanas con esta implementación.
