# L507: segmentación reproducible hasta densificación

## Referencia y recuperación

El piloto original queda en `l507-baseline-sparse-dense-v1` (commit `02c1c7b`),
con el checkout original `/data/photogrammetry/repo` y el experimento
`outputs/cuadra-025/l507_012_0_10s_v1` preservados. La integración se realiza en
otro worktree: `/data/photogrammetry/segmentation-repo`, rama
`feat/l507-segmentation`.

El checkpoint de L507_012 está en
`/data/photogrammetry/checkpoints/baseline-before-segmentation-v1/`:

- `baseline.bundle`: historia Git completa, verificada mediante clon restaurado.
- `photogrammetry-baseline.tar`: imagen certificada, para `docker image load`.
- `checkpoint.json`: IDs y SHA256 del bundle y del archivo de imagen.
- `l507_012_0_10s_v1/`: copia verificada del experimento completo.
- `baseline-results-sha256.json`: checksums de sus archivos.
- `node.env` y `host-config/`: configuración local, no material para copiar
  indiscriminadamente a otro equipo; configuración privilegiada en directorio 700.

Para recuperar: usar el checkout/etiqueta original, su `node.env` y la imagen
fijada. Si falta la imagen, verificar el checksum y cargar el archivo exportado.
No restaurar bases de datos de Docker/containerd en caliente. La segmentación
no requiere modificar los servicios ni el montaje del host.

## Entornos y pesos

La reconstrucción usa `photogrammetry:l507-v1-pycolmap`; segmentación usa
`photogrammetry-seg:l507-v1`. La receta nueva es
`containers/Dockerfile.segmentation`: Ubuntu 24.04, entorno virtual Python,
PyTorch 2.7.1 cu128, torchvision 0.22.1, Transformers 4.44.2 y SAM 3 en el commit
`660a5e9e1b8b4c02c0ad97229b88a09a6e4ff5b7`. Las dependencias adicionales están
fijadas en `containers/segmentation-requirements.txt`.

```bash
docker build -f containers/Dockerfile.segmentation -t photogrammetry-seg:l507-v1 .
```

No se incorporan pesos ni tokens a las imágenes. Guardar los pesos bajo
`${PG_DATA_ROOT}/models/{segformer,sam3}`. `tools/fetch_models.py` descarga
revisiones fijadas y crea `weights-manifest.json` con checksums. SAM 3 requiere
acceso autorizado a `facebook/sam3`; introducir el token en privado, sin
incluirlo en comandos versionados. Con un contenedor de segmentación cargado,
una sesión interactiva puede usar `getpass` y pasar el token solo en memoria:

```bash
docker run --rm -it --entrypoint python3 -u "$(id -u):$(id -g)" \
  --mount type=bind,src=/data/photogrammetry,dst=/data/photogrammetry \
  -e HOME=/tmp -e HF_HOME=/data/photogrammetry/models/hf-cache \
  -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 \
  -w /data/photogrammetry/segmentation-repo photogrammetry-seg:l507-v1 \
  -c 'import getpass,os,runpy,sys; os.environ["HF_TOKEN"]=getpass.getpass("HF token: "); sys.argv=["fetch_models", "--root", "/data/photogrammetry/models"]; runpy.run_path("tools/fetch_models.py", run_name="__main__")'
```

La inferencia del lanzador está forzada a modo offline. El entorno del ensayo
añade `PG_NETWORK=none` para deshabilitar la red del contenedor durante las
pruebas (la descarga de pesos se ejecuta por separado con red). Se verifica la
integridad de pesos antes de aceptar la identidad del experimento.

## Configuración y ejecución

Copiar `containers/lab-node.env.example` fuera de Git, con permisos 600, y
completar el tag real y los IDs certificados. En L507_012 se usa
`/data/photogrammetry/node-segmentation.env`; el archivo original no se sustituye.

Los YAML de `configs/experiments/segmentation-025-0-10s/` contienen cuatro
variantes: `control`, `segformer`, `sam3` y `combined`. Comparten intervalo
[0,10), 2 FPS, 1280 px, cámaras SIMPLE_RADIAL y parámetros de SfM/OpenMVS.
Las rutas se expanden desde `${PG_DATA_ROOT}`; una variable ausente es un error.
Los nombres de experimento no incluyen identidad de máquina ni IP.

```bash
export PG_ENV_FILE=/data/photogrammetry/node-segmentation.env
bash containers/lab-run.sh doctor --profile dense \
  --scene cuadra-025 --config configs/experiments/segmentation-025-0-10s/combined.yaml
bash containers/lab-segment.sh doctor --profile segmentation \
  --scene cuadra-025 --config configs/experiments/segmentation-025-0-10s/combined.yaml
bash containers/lab-experiment.sh --scene cuadra-025 \
  --config configs/experiments/segmentation-025-0-10s/combined.yaml
```

El último comando ejecuta tres procesos secuenciales: reconstrucción
`frames,telemetry`; segmentación `masks`; reconstrucción
`sfm,undistort,dense,metrics`. En `control`, máscaras se omite automáticamente.
Para sobrevivir a una desconexión, lanzar este comando desde un supervisor como
el descrito en `l507-pilot.md`, con nombre de unidad y log propios por variante.
Para ejecutar las cuatro variantes en orden y detenerse ante cualquier fallo:

```bash
PG_ENV_FILE=/data/photogrammetry/node-segmentation.env \
  bash containers/lab-segmentation-suite.sh cuadra-025
```

El manifiesto schema 2 fija ambas imágenes, código, configuración, entradas y
pesos. Cada marcador registra imagen y rol reales. No se reutilizan experimentos
si cambia alguno de esos componentes, ni se migran manifiestos antiguos en
silencio: elegir un `experiment.name` nuevo. Al reanudar también se comprueba que
los productos críticos sigan presentes. No usar `--force` sobre el piloto.

## Máscaras y geometría

- SegFormer excluye personas, vehículos y cielo.
- SAM 3 usa prompts de esos objetos, cables y postes; umbral inicial 0.5.
- Se conserva vegetación. La dilatación inicial es 3 píxeles a 1280 px.
- En combinado, se excluye cualquier píxel excluido por alguno de los modelos.
- COLMAP recibe las máscaras originales (`0=ignorar`, `255=usar`).
- Después de undistort se generan otras máscaras usando las cámaras originales
  y las nuevas, centros de píxel COLMAP, vecino más cercano y bordes inválidos 0.
- OpenMVS recibe `colmap/undistorted_masks`, no las máscaras originales. Se
  comprueban nombres planos, dimensiones y valores binarios.

Esto requiere PyCOLMAP 3.11.1 en el contenedor de reconstrucción. En esta imagen
se recorren los IDs de imágenes registradas; iterar `ImageMap.values()` en el
binario histórico produjo un aborto al finalizar el iterador. Las pruebas reales
validan el recorrido utilizado. No se ha certificado aquí el contenedor Apptainer
de Khipu.

## Verificación

```bash
PG_ENV_FILE=/data/photogrammetry/node-segmentation.env \
  bash containers/docker-run.sh python3 -B -m unittest discover -s tests -v
```

`tools/segmentation_smoke.py` prueba ambos modelos en tres frames offline y
guarda overlays y VRAM pico. `tools/check_dense_mask_consumption.py` ejecuta dos
densificaciones aisladas sobre idénticas cámaras/imágenes: máscaras blancas
deben dar puntos; negras deben dar cero. Usa workspaces nuevos, sin depthmaps
heredados. Los originales solo se leen.

`tools/compare_segmentation.py` comprueba que los 20 JPG sean idénticos entre
variantes, revisa keypoints dentro de exclusiones, recorre los PLY y genera
previews. Sus proyecciones PCA son independientes: no constituyen una alineación
geométrica entre reconstrucciones ni una medida en metros.

## Replicar en otra hija

1. Identificar el nodo por tag/MAC y verificar su partición. Obtener su UUID;
   nunca copiar el de L507_012. No formatear discos existentes.
2. Preparar explícitamente montaje bajo demanda, permisos y almacenamiento de
   Docker/containerd en el volumen. Si existen datos del runtime, su migración
   necesita respaldo y verificación; un clon Git no hace esa migración.
3. Clonar la rama/release publicada, o restaurar el bundle certificado si no hay
   autenticación GitHub. El clon no contiene imágenes, datos ni pesos.
4. Cargar las imágenes distribuidas por archivo con checksum e inspeccionar sus
   IDs, o construir las recetas. La imagen de reconstrucción del ensayo está
   certificada por archivo/ID; no se certifica aquí reconstruirla completa desde
   cero mediante el Dockerfile principal.
5. Transferir los modelos y comprobar sus manifiestos, o descargarlos con acceso
   autorizado. Colocar el video/SRT de prueba en `datasets/raw/cuadra-025/drone`.
6. Crear el `node.env` local desde el ejemplo con tag/IDs/rutas propios. Si se usa
   el helper de volumen, instalarlo con configuración root-owned basada en
   `deploy/lab-node.json.example`, usando el UUID real y conservando los servicios
   deshabilitados al arranque. Nunca instalar el ejemplo sin rellenarlo.
7. Ejecutar ambos `doctor`, pruebas y ensayo corto. Para comparar contra otro
   nodo, usar outputs propios: no compartir un directorio de experimento activo.

En esta fase no se despliega ni se modifica ninguna otra hija.
