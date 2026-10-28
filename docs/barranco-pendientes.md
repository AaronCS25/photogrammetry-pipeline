# Barranco: pendientes de ingesta y preparación SfM

## Descargas pendientes

Los siguientes MP4 locales contienen una página HTML de Google Drive
(`Google Drive - Quota exceeded`, 2040 bytes), no datos de video:

- `023/DJI_20261024134609_0531_D.MP4`
- `024/DJI_20261024124253_0524_D.MP4`
- `024/DJI_20261024124457_0525_D.MP4`
- `024/DJI_20261024124600_0526_D.MP4`
- `025/DJI_20261024124736_0527_D.MP4`

Pendiente: validar tipo de contenido y archivo antes de dar una descarga por
completa; recuperar estos cinco originales cuando Drive permita descargarlos;
extraer frames y SIFT únicamente para ellos. No descargar proxies LRF.
Conservar los resultados válidos existentes.

## Inventario local verificado

63 videos con SIFT, 5141 frames con descriptores. Resolución de cámaras:
3840x2160; modelo guardado: SIMPLE_RADIAL (ID COLMAP 2).

| Manzana | Videos con SIFT / MP4 locales | Frames |
|---|---:|---:|
| 001 | 4/4 | 244 |
| 002 | 2/2 | 142 |
| 003 | 5/5 | 505 |
| 004 | 3/3 | 294 |
| 005 | 9/9 | 651 |
| 007 | 5/5 | 310 |
| 008 | 5/5 | 396 |
| 009 | 6/6 | 468 |
| 012 | 6/6 | 520 |
| 013 | 5/5 | 478 |
| 017 | 2/2 | 334 |
| 018 | 8/8 | 361 |
| 023 | 3/4 | 438 |
| 024 | 0/3 | 0 |
| 025 | 0/1 | 0 |

La carpeta local 006 no contiene MP4. No hay carpetas locales para varios otros
IDs entre 001 y 025. Este inventario no certifica que las 25 manzanas tengan
material descargado; contrastar las carpetas sin videos con Drive.

## Antes de SfM (plan, sin ejecución)

- Conservar las BD originales por video y trabajar sobre copias por manzana.
- Dar nombres únicos a los frames (prefijo de video + índice temporal).
- Consolidar cámaras, imágenes, keypoints y descriptores remapeando IDs.
- Conservar SIMPLE_RADIAL inicialmente; no reinterpretar sus parámetros como
  OPENCV (el experimento barranco_full usa OPENCV).
- Matching temporal dentro de cada clip y matching entre clips para conectar
  recorridos y cerrar bucles; verificar geométricamente los pares.
- Mapper incremental con ajuste de haces local/global; evaluar registro,
  componentes, tracks y reproyección antes de aceptar un modelo.
- El run_sfm actual elimina la BD y recalcula SIFT: adaptar la reutilización
  de features antes de invocarlo con estas BD.
- La extracción existente usa COLMAP 3.13; el pipeline espera flags de 3.11.
  Resolver compatibilidad de CLI y esquema de BD antes de integrar.
- La selección actual del mejor modelo usa tamaño de images.bin como proxy;
  comprobar número real de imágenes registradas y todos los componentes.

No se han lanzado jobs de matching ni mapper para este plan.
