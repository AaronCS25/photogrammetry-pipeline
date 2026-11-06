# Piloto local L507: nube sparse y densa, sin segmentación

## Alcance

Equipo piloto: **L507_012** (resolver siempre por TXT/MAC; la IP es DHCP).
La maestra solo controla por SSH. Este piloto no instala un planificador ni
modifica el toolkit de `/home/exitoso/lab`.

La partición ext4 grande se monta **bajo demanda** en `/data`. No hay entrada de
automontaje en fstab ni servicios Docker habilitados al arrancar. El proyecto,
video, resultados, temporales y almacenamiento Docker/containerd están bajo
`/data/photogrammetry`. El TXT original del home se conserva para el inventario.

## Disposición del host

```text
/data/photogrammetry/
  NODE_TAG.txt
  node.env                      # configuración local, sin credenciales
  repo/                         # clon Git con origin de GitHub
  datasets/raw/cuadra-025/drone/ # MP4, SRT y manifiestos SHA256
  outputs/cuadra-025/            # un directorio por experimento
  scratch/                      # TMPDIR del contenedor
  models/                       # reservado; no se necesitan pesos para este piloto
  runtime/docker/
  runtime/containerd/
  logs/
  setup-backups/                # configuración original del host
```

Se instala una copia root-owned de `deploy/lab_volume.py` en
`/usr/local/sbin/photogrammetry-volume`, con identidad en
`/etc/photogrammetry/node.json`. Es el puente mínimo fuera del volumen para
poder abrirlo cuando el código aún no está montado.

```bash
sudo /usr/local/sbin/photogrammetry-volume open
/usr/local/sbin/photogrammetry-volume status
# Al terminar, desde un directorio fuera de /data y sin trabajos/builds activos:
sudo /usr/local/sbin/photogrammetry-volume close
```

`close` rechaza contenedores/builds activos; nunca fuerza el desmontaje. Un
archivo/cwd abierto puede impedirlo: se mantiene montado y se informa del error.
No se reinicia ni modifica el arranque del equipo.

**Permisos, no cifrado:** el directorio del proyecto tiene modo 700. No protege
contra root ni contra otra persona usando la misma cuenta `utec_alumno`.

## Preparación inicial / cambios del sistema

La preparación requiere sudo y no se ejecuta al clonar. Antes de replicarla,
verificar que la partición no es `/`, su contenido, tag, MAC, espacio y ausencia
de otros usuarios de Docker. No formatear ni asumir un número de partición.

En el piloto se guardaron configuraciones anteriores en `setup-backups/`, se
detuvieron Docker/socket/containerd sin contenedores activos y se copiaron sus
almacenes con `rsync -aHAX --numeric-ids` al volumen.

Configuración aplicada:

- `/etc/docker/daemon.json`: conserva runtime NVIDIA; `data-root` apunta a
  `/data/photogrammetry/runtime/docker`, logs `local` limitados a 3 x 20 MB.
- `/etc/containerd/config.toml`: `root` apunta a
  `/data/photogrammetry/runtime/containerd`; `state` sigue siendo efímero en `/run`.
- Drop-in `photogrammetry-volume.conf` para Docker, socket y containerd:
  `ConditionPathIsMountPoint=/data` y existencia de `NODE_TAG.txt`.
- Docker, su socket y containerd quedan **disabled**, no masked. `open` los inicia.

Tras verificar las imágenes, GPU y reconstrucción, los almacenes originales se
copiaron a `setup-backups/runtime-before-migration/`, se verificaron con rsync en
modo checksum y se retiraron las copias antiguas de `/var/lib`. Es un rollback
frío adicional de unos 46 GiB en el volumen de datos. Antes de cualquier rollback:
cerrar los trabajos, detener servicios y restaurar ambos almacenes y ambas
configuraciones de forma conjunta. No sustituir bases de datos de containerd en
caliente. El checkout original del home se respaldó en
`setup-backups/home-repo-before-migration/`; `~/photogrammetry-pipeline` ahora es
un enlace al checkout del volumen, no una segunda copia de trabajo.

## Imagen de reconstrucción

La imagen antigua `photogrammetry:local` se conserva. El piloto añade bindings
desde COLMAP **3.11.1**, commit comprobado
**`682ea9ac4020a143047758739259b3ff04dabe8d`**.

```bash
docker build --build-arg J=4 -f containers/Dockerfile.pycolmap \
  -t photogrammetry:l507-v1-pycolmap .
```

Esta receta **requiere** la imagen base local validada, con CUDA 12.8.1 y COLMAP
3.11.1. No descargar un wheel CPU de PyCOLMAP: se compila contra COLMAP CUDA.
La receta completa `containers/Dockerfile` también se actualiza para CUDA 12.8.1,
arquitecturas `86;89;120`, parche OpenMVS y bindings; su build completo debe
certificarse antes de usarla como alternativa al baseline del piloto.

Configurar `node.env` con `containers/lab-node.env.example`. El wrapper verifica
el montaje **antes de contactar Docker**, acepta SSH sin TTY y mantiene usuario,
TMPDIR y datos en el volumen. La imagen realmente usada se registra por ID en
los manifiestos y métricas. El tag local no equivale a una publicación en registry.
El `node.env` del piloto fija también `PG_EXPECTED_IMAGE_ID`: el wrapper rechaza
un tag que apunte silenciosamente a otra imagen. `PG_QUIET_ENTRYPOINT=1` evita
el banner inicial de CUDA para que `doctor --json` entregue JSON limpio.

## Video y configuración del ensayo

Entrada pública: carpeta Drive `1ovMEagaEqT_Rq2M19x1RV7tM7JWr0Y0u` (025).

```bash
python3 tools/fetch_drive.py --file-id 1iq2bH3oILP0RIAjvr2xFyKpBjIIgSLIX \
  --output /data/photogrammetry/datasets/raw/cuadra-025/drone/DJI_20261024124736_0527_D.MP4
python3 tools/fetch_drive.py --file-id 13ZZrOUH78-AInWRArjoJt0Z9Pj3Yavbt \
  --output /data/photogrammetry/datasets/raw/cuadra-025/drone/DJI_20261024124736_0527_D.SRT
```

El downloader conserva `.part` si falla; no publica HTML de error como MP4 ni
sobreescribe originales sin verificar. Una repetición reutiliza archivos cuyo
checksum coincide con su manifiesto. No implementa todavía reanudación HTTP
Range: inspeccionar un `.part` antes de retirarlo y volver a descargar.

La configuración `configs/experiments/l507_012_0_10s.yaml` usa 2 FPS, intervalo
original `[0,10)`, JPG de 1280 px, matching exhaustivo (solo ~20 imágenes), nube
densa OpenMVS reducida. No pide máscaras, georef, malla, refinado ni textura.
El MP4 original es 3840x2160, H.264 High, ~29.97 FPS y dura 46.046 s.

```bash
./containers/lab-run.sh doctor --profile dense --scene cuadra-025 \
  --config configs/experiments/l507_012_0_10s.yaml --json
./containers/lab-run.sh validate --scene cuadra-025 \
  --config configs/experiments/l507_012_0_10s.yaml
./containers/lab-run.sh run --scene cuadra-025 \
  --config configs/experiments/l507_012_0_10s.yaml \
  --stages frames,telemetry,sfm,undistort,dense,metrics
```

Los comandos no dependen de una terminal interactiva. Para trabajos largos usar
un supervisor: este piloto no es aún el orquestador. La prueba real se lanzó con
una unidad transitoria de systemd, usuario `utec_alumno`, que sobrevivió a cerrar
la conexión SSH. Ejemplo (cambiar el nombre de unidad si ya existe):

```bash
sudo systemd-run --unit=photogrammetry-pilot-025-v1 \
  --uid=utec_alumno --gid=utec_alumno \
  --working-directory=/data/photogrammetry/repo \
  --property=Type=exec --property=RuntimeMaxSec=1800 \
  --property=StandardOutput=append:/data/photogrammetry/logs/pilot-025-0-10s.log \
  --property=StandardError=append:/data/photogrammetry/logs/pilot-025-0-10s.log \
  /data/photogrammetry/repo/containers/lab-run.sh run \
  --scene cuadra-025 --config configs/experiments/l507_012_0_10s.yaml \
  --stages frames,telemetry,sfm,undistort,dense,metrics
```

La unidad no se habilita al arrancar. Logs por etapa en `outputs/.../logs/`.

Resultados esperados:

```text
outputs/cuadra-025/l507_012_0_10s_v1/
  run_manifest.json
  config_resolved.yaml
  frames/drone/*.jpg
  colmap/database.db
  colmap/sparse/<modelo>/{cameras,images,points3D}.bin
  colmap/sparse.ply
  colmap/undistorted/
  mvs/scene_dense.{mvs,ply}
  metrics/{metrics.json,timings.csv}
  logs/
```

## Reanudación y límites

El perfil activa `runtime.verify_cache`: lock exclusivo y hashes de datos,
configuración, código e imagen. Cambiar cualquiera exige un nombre nuevo de
experimento; no se permite reutilizar silenciosamente marcadores antiguos.
Repetir exactamente el comando reutiliza etapas terminadas. `--force` invalida
marcadores posteriores. Esto es una protección conservadora del piloto, no un
caché granular de artefactos compartidos entre imágenes de segmentación/MVS.

Se comprueban 20 GiB libres antes de comandos externos y cada ~2 s durante su
ejecución. Si falta espacio, se termina solo el grupo de procesos del comando.
No es una cuota dura ni garantía frente a otro proceso llenando el disco.
El wrapper limita CPU/RAM según node.env; no reserva ni limita VRAM por hardware.

```bash
./containers/docker-run.sh python3 -B -m unittest discover -s tests -v
```

Para usar las opciones del nodo también en comandos arbitrarios del contenedor:

```bash
PG_ENV_FILE=/data/photogrammetry/node.env ./containers/docker-run.sh \
  python3 tools/preview_clouds.py \
  --run-dir /data/photogrammetry/outputs/cuadra-025/l507_012_0_10s_v1
```

Las vistas previas y el informe de integridad quedan en `previews/clouds/` del
experimento. El lector soporta las listas por vértice de OpenMVS; no confundir
ese PLY con una matriz de registros de longitud fija. Son proyecciones PCA para
inspección, no transformaciones aplicadas al modelo exportado.

Resultado certificado del ensayo: [l507-pilot-result.md](l507-pilot-result.md).

Pendientes posteriores: registry de imágenes, orquestación por tags, usuario
de servicio aislado, pesos/inferencia SAM3 y SegFormer, caché por etapa, mallas
y alineación de cuadras. No se habilitan por esta prueba de nubes.
