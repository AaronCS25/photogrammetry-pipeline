# Resultado del piloto: L507_012, cuadra 025, 0–10 s

Observación del ensayo ejecutado el 2026-11-06. No es un benchmark del video
completo ni una reconstrucción final de toda la cuadra.

## Entorno

- Nodo: `L507_012`, MAC `f4:f1:9e:42:71:e1` (IP de la sesión: 10.100.41.53).
- GPU: NVIDIA RTX PRO 4000 Blackwell, 24467 MiB, driver 595.99.02.
- Kernel: 6.17.0-1033-oem; CPU Intel Core Ultra 9 285K; RAM del host ~64 GB.
- Contenedor limitado a 8 CPU y 48 GiB de RAM; una GPU.
- Imagen: `photogrammetry:l507-v1-pycolmap`.
- ID: `sha256:1a3c25a01680253192d7b95bb57abb3c2a680a1db8d9888c6fe533fcf08e98e0`.
- CUDA 12.8.1; COLMAP/PyCOLMAP 3.11.1 con CUDA; OpenMVS 2.3.0.
- PyYAML 6.0.2, NumPy 1.26.4 y Pillow 10.4.0.
- Base Git: `293e30ac83113ddf63c120c0694e2a1bcb1aeab7` más cambios locales
  del piloto. El manifiesto de ejecución contiene hashes del código real.

## Entrada

- `DJI_20261024124736_0527_D.MP4`, 708476241 bytes (675.7 MiB).
- SHA256: `795aff1f9f45cb64c952199bc874ce83b69089eaeee412fcf604c0fe5819476c`.
- H.264 High, 3840x2160, 30000/1001 FPS, 46.046 segundos.
- SRT: 413911 bytes; SHA256
  `819d2d3e5eb0d9ce573b95e46dd8aaac6cc73a0305031973a676f5294fe7bd2e`.
- Extracción: primeros diez segundos a 2 FPS, JPG de lado mayor 1280 px.
- Sin segmentación, georreferenciación, malla ni textura.

## Resultados

| Medida | Valor observado |
|---|---:|
| Frames | 20 |
| Imágenes registradas | 20 / 20 |
| Modelos sparse | 1 |
| Cámaras/intrínsecos | 1 |
| Puntos sparse | 19707 |
| Observaciones | 141487 |
| Longitud media de track | 7.17953 |
| Reproyección media | 0.35814 px |
| Puntos densos OpenMVS | 1685281 |
| PLY sparse | 295784 bytes |
| PLY denso | 123759805 bytes |
| Carpeta del experimento | ~605 MiB, antes de añadir previews |

Tiempos de pared registrados por las etapas (no incluyen descargar/build):

| Etapa | Segundos |
|---|---:|
| Frames | 1.95 |
| Telemetría | 0.01 |
| SfM | 14.76 |
| Undistort | 0.29 |
| Densificación y conversión | 8.41 |
| Total de etapas | 25.42 |

El log confirma SIFT GPU y la inicialización CUDA de DensifyPointCloud en
Blackwell. OpenMVS emite `MapSMtoCores for SM 12.0 is undefined`: es el estimador
antiguo de cores/SM, no un fallo de carga CUDA. La densificación terminó con
estado cero y una nube no vacía. Queda anotado para mantenimiento de la receta.

## Comprobaciones realizadas

- `doctor --profile dense`: correcto, también tras cerrar y reabrir el volumen.
- 11 pruebas de regresión: correctas.
- PyCOLMAP abrió el modelo real y confirmó las cámaras, puntos y métricas.
- Se recorrió el PLY denso, incluidas sus listas de vistas/pesos, y se generaron
  proyecciones con coordenadas finitas en la muestra.
- Inspección visual de frames y proyecciones sparse/densas: se reconocen las
  cubiertas, calles y fachadas observadas. Hay huecos/zonas poco observadas;
  no se presupone cobertura completa, escala métrica ni alineación final.
- Repetición exacta: reutilizó las cinco etapas y recalculó métricas, sin
  repetir extracción/reconstrucción.
- Servicio supervisor: terminó con `Result=success`, `ExecMainStatus=0`.
- Cierre bajo demanda: volumen desmontado y servicios inactivos; reapertura
  recuperó imágenes, GPU y datos. No hubo reinicio del equipo.

## Ubicación

Todo bajo `/data/photogrammetry/outputs/cuadra-025/l507_012_0_10s_v1/`:

- `colmap/sparse.ply`
- `colmap/sparse/0/` (modelo COLMAP original)
- `mvs/scene_dense.ply` y workspace de OpenMVS
- `metrics/metrics.json`, `metrics/timings.csv`
- `run_manifest.json`, `config_resolved.yaml`
- `previews/clouds/{input-frames,sparse,dense}.png`
- `previews/clouds/cloud-inspection.json`

Al finalizar la migración: ~61 GiB libres en `/`, ~362 GiB en `/data`, incluyendo
un respaldo frío adicional de ~46 GiB del runtime anterior. No se ha eliminado
ese respaldo, ni publicado imágenes, ni hecho commit de los cambios.
