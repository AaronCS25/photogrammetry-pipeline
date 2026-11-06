# Resultado: SegFormer + SAM 3 hasta densificación, L507_012

Ensayo del 2026-11-06, cuadra 025, intervalo [0,10), 2 FPS, 20 JPG de 1280x720.
Las cuatro variantes tienen exactamente los mismos frames, verificados por
SHA256. No incluye malla, textura, georreferenciación ni el video completo.

## Versiones preservadas

- Piloto original: `02c1c7b`, etiqueta `l507-baseline-sparse-dense-v1`.
- Implementación de segmentación: `1670607`, rama `feat/l507-segmentation`.
- Reconstrucción: `sha256:1a3c25a01680253192d7b95bb57abb3c2a680a1db8d9888c6fe533fcf08e98e0`.
- Segmentación, construida desde la receta versionada:
  `sha256:9679fd9e51dac6b80cd7fdd75d43b3976c783c81393c25f407e24e14bd127042`.
- SegFormer revisión `2c6f153e4c23c229e2fa2b188eb250607e030cd8`, 338898408 bytes.
- SAM 3 revisión `3c879f39826c281e95690f02c7821c4de09afae7`, 3450088084 bytes.
- SAM 3 código: `660a5e9e1b8b4c02c0ad97229b88a09a6e4ff5b7`.
- GPU RTX PRO 4000 Blackwell, driver 595.99.02; PyTorch 2.7.1 cu128;
  Transformers 4.44.2; COLMAP/PyCOLMAP 3.11.1 y OpenMVS 2.3.0.
- Límites de los contenedores: 8 CPU y 48 GiB de RAM. Inferencia y reconstrucción
  ejecutadas con `--network none`; la descarga de pesos fue un proceso separado.

## Comparación

| Variante | Registradas | Sparse | Densa | Reproyección px | Píxeles excluidos, media | Tiempo de etapas s |
|---|---:|---:|---:|---:|---:|---:|
| Control nuevo | 20/20 | 19701 | 1685593 | 0.357748 | 0% | 25.18 |
| SegFormer | 20/20 | 17540 | 1553149 | 0.349742 | 9.20% | 37.19 |
| SAM 3 | 20/20 | 17266 | 1538227 | 0.350962 | 9.28% | 38.47 |
| Combinado | 20/20 | 16605 | 1496213 | 0.350227 | 12.77% | 40.77 |

Los tiempos excluyen descargar pesos, construir imágenes, verificar hashes entre
procesos, diagnóstico y previews. No se mezclan con el tiempo de punta a punta.
El piloto original tenía 19707 puntos sparse y 1685281 densos: el control nuevo
es consistente, sin exigir identidad bit a bit entre ejecuciones GPU.

## Comprobaciones realizadas

- 26 pruebas automatizadas en la imagen de reconstrucción: correctas, sin skips.
  Incluyen I/O de modelos COLMAP reales, identidad y distorsión radial de máscaras,
  bordes inválidos, máscara no binaria, alias borrado, pesos corruptos, cambios de
  revisión/prompts/imagen, roles incompatibles, concurrencia y reanudación.
- Imagen de segmentación construida desde cero; imports, CUDA y `pip check` pasan.
- Ambos `doctor` pasan y verifican pesos. Prueba de inferencia real con ambos
  modelos en tres frames, sin red, produce las mismas fracciones de máscara que
  la imagen histórica usada inicialmente para calibración.
- En la prueba corta: pico asignado ~2175 MiB para SegFormer y ~5466 MiB para
  SAM 3; pico reservado ~2500 y ~6014 MiB respectivamente. No son máximos medidos
  de todo el ensayo de veinte imágenes.
- Inspección visual de las superposiciones: exclusión de vehículos, retención
  general de edificios y vegetación. Existen objetos pequeños y bordes ambiguos;
  no se afirma precisión perfecta de segmentación ni geometría oculta recuperada.
- OpenMVS sobre idénticas cámaras/imágenes y cachés independientes:
  máscaras blancas -> 1684076 puntos; negras -> 0 puntos. Ambas llamadas retornan
  cero: la validación adicional de nube no vacía es necesaria en el pipeline.
- Las máscaras densas se reproyectan a las cámaras de cada modelo. Como ejemplo,
  el workspace de referencia es 1256x706, diferente de los 1280x720 originales.
- Consulta de la base COLMAP: 0 keypoints en el interior de las regiones
  excluidas, sobre 195434 (SegFormer), 192898 (SAM 3) y 184226 (combinado).
  Se descarta una franja de un píxel para no confundir convenciones de borde.
- Se recorrieron los ocho PLY de las cuatro variantes y se comprobaron registros,
  listas de OpenMVS y coordenadas finitas de la muestra de preview.
- Repetición del combinado: seis marcadores y dos PLY mantuvieron SHA256 y mtime;
  solo se recalcularon métricas. No volvió a ejecutar inferencia, SfM o denso.

## Resultados y uso

En `/data/photogrammetry/outputs/cuadra-025/`:

```text
seg025_0_10s_control_v1/
seg025_0_10s_segformer_v1/
seg025_0_10s_sam3_v1/
seg025_0_10s_combined_v1/
segmentation-comparison-v1/comparison.json
segmentation-smoke-certified-v1/{overlays.png,smoke.json}
```

Cada experimento contiene `colmap/sparse.ply`, `mvs/scene_dense.ply`, métricas,
manifiesto de procedencia, máscaras originales/transformadas y previews. Las
proyecciones PCA son independientes; no se han calculado distancias entre nubes
en coordenadas sin alinear ni se presupone escala métrica.

Como siguiente candidato para ampliar el tramo se propone el combinado: mantiene
20/20 cámaras y excluye más objetos. La comparación actual demuestra integración
y estabilidad, no superioridad geométrica frente a un ground truth.

Guía de ejecución, recuperación y réplica: [l507-segmentation.md](l507-segmentation.md).
Los artefactos de distribución se guardan fuera de Git en
`/data/photogrammetry/checkpoints/segmentation-release-v1/`; consultar su
`release.json` para los hashes certificados. La publicación en GitHub depende
de autenticar una cuenta con permiso de escritura; el bundle permite clonar
localmente sin esa autenticación.
