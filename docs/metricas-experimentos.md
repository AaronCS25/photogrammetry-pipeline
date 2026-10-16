# Métricas de los experimentos (septiembre–octubre 2026)

Tabla consolidada de todas las corridas del pipeline en Khipu que llegaron a la
etapa `metrics` (fuente: `outputs/<escena>/<experimento>/metrics/metrics.json`,
exportadas el 2026-10-15 con `python3 -m pipeline report`). El CSV completo, con
tiempos por etapa, máscaras por backend y parámetros de limpieza, está en
`docs/metricas-experimentos.csv`. Para regenerar: `pipeline report` en Khipu y
el script de consolidación (ver sección final).

Columnas: *Registradas* = imágenes en el modelo SfM principal (COLMAP puede
producir varios modelos desconectados; aquí se cuenta el mayor). *Puntos
densos* = nube de OpenMVS. *Caras malla final* = la última malla producida
(limpia > refinada > sin refinar). *% enmasc.* = fracción media de píxeles
ignorados por las máscaras. *Tiempo* = suma de las etapas ejecutadas en esa
corrida (las etapas reutilizadas de una corrida anterior no cuentan: p. ej.
`hornero_drone_v2_geo`..`v4_clean` reutilizan el sparse de `v1`).

| Escena | Experimento | Fuentes (fotos) | Registradas | % | Puntos sparse | Reproy. (px) | Puntos densos | Caras malla final | Malla | Máscaras | % enmasc. | Georef | Tiempo (h) | GPU |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| barranco_drone | barranco_drone_v1 | main:393 | 393 | 100.0 | 275 083 | 0.75 | 22 535 949 | 3 016 724 | refinada | — | — | — | 1.4 | — |
| barranco_drone | barranco_drone_v2_dense0 | main:393 | 393 | 100.0 | 275 083 | 0.75 | 83 962 416 | 2 258 667 | refinada | — | — | — | 1.74 | — |
| barranco_full | barranco_full_masked_sam3_v1 | drone:393, phone:534 | 925 | 99.8 | 401 771 | 1.05 | 39 372 224 | 5 537 949 | refinada | segformer, sam3 | 34.4 | — | 3.69 | RTX A6000 |
| barranco_full | barranco_full_v1 | drone:393, phone:534 | 926 | 99.9 | 573 731 | 1.03 | 54 627 295 | 7 050 733 | refinada | — | — | — | 4.0 | A100-PCIE-40GB |
| barranco_phone | barranco_phone_masked_v1 | horizontal:534 | 533 | 99.8 | 232 915 | 1.29 | 25 934 485 | 8 177 039 | refinada | segformer | 31.6 | — | 2.61 | RTX A6000 |
| barranco_phone | barranco_phone_v1 | horizontal:534 | 467 | 87.5 | 248 969 | 1.3 | 27 755 148 | 9 647 282 | refinada | — | — | — | 2.81 | — |
| hornero | hornero_full_v1 | drone:77, phone:148 | 153 | 68.0 | 53 522 | 0.98 | 12 436 203 | 4 297 135 | refinada | segformer, sam3 | 23.9 | — | 1.41 | A100-PCIE-40GB |
| hornero | hornero_full_v2 | drone:77, phone:148 | 225 | 100.0 | 174 954 | 1.12 | 17 554 797 | 6 339 216 | refinada | segformer, sam3 | 25.2 | — | 3.31 | A100-PCIE-40GB |
| hornero | hornero_full_v3 | drone:77, phone:148 | 225 | 100.0 | 183 085 | 1.13 | 18 813 383 | 6 699 829 | refinada | sam3, segformer | 20.0 | — | 2.83 | A100-PCIE-40GB |
| hornero_drone | hornero_drone_v1 | drone:77 | 77 | 100.0 | 81 572 | 1.37 | 8 365 438 | 1 732 553 | refinada | sam3 | 10.7 | — | 0.75 | RTX A6000 |
| hornero_drone | hornero_drone_v2_geo | drone:77 | 77 | 100.0 | 81 572 | 1.37 | 7 345 263 | 1 437 210 | refinada | sam3 | 10.7 | enu (métrico) | 0.7 | RTX A6000 |
| hornero_drone | hornero_drone_v3_tiles | drone:77 | 77 | 100.0 | 78 514 | 1.36 | 6 959 633 | 1 445 744 | refinada | sam3 | 13.2 | enu (métrico) | 0.92 | RTX A6000 |
| hornero_drone | hornero_drone_v4_clean | drone:77 | 77 | 100.0 | 78 514 | 1.36 | 4 887 386 | 1 337 748 | refinada | sam3 | 13.2 | enu (métrico) | 1.06 | RTX A6000 |
| hornero_phone | hornero_phone_v1 | phone:148 | 148 | 100.0 | 95 306 | 0.9 | 9 090 275 | 3 435 673 | refinada | segformer, sam3 | 29.2 | — | 1.09 | RTX A6000 |
| manzana_01 | phone_v1 | phone:557, phone_v:13 | 551 | 96.7 | 530 777 | 1.29 | 39 391 040 | 3 487 323 | refinada | segformer, sam3 | 21.3 | plane | 4.03 | RTX A6000 |
| manzana_02 | phone_v1 | phone:626, phone_v:6 | 626 | 99.1 | 675 743 | 1.3 | 50 978 524 | 3 903 948 | refinada | segformer, sam3 | 24.3 | plane | 4.95 | RTX A6000 |
| manzana_03 | phone_v1 | phone:466 | 466 | 100.0 | 359 905 | 1.29 | 34 791 630 | 1 398 412 | limpia | segformer, sam3, wires | 40.3 | plane | 3.21 | RTX A6000 |
| sv_7c34 | streetview_single_v1 | fachadas:36 | 4 | 11.1 | 325 | — | — | — | — | — | — | — | — | — |
| sv_7c34_three | streetview_single_v1 | fachadas:92 | 33 | 35.9 | 2 517 | 0.82 | 191 064 | 97 963 | limpia | segformer, manual, wires | 41.8 | enu (métrico) | 0.11 | A100-PCIE-40GB |
| sv_manzana_a | streetview_v1 | fachadas:246 | 180 | 73.2 | 43 239 | 0.91 | 3 514 387 | 338 414 | refinada | segformer, manual | 50.6 | enu (métrico) | 0.77 | RTX A6000 |
| sv_manzana_b | streetview_v1 | fachadas:240 | 135 | 56.2 | 16 998 | 0.87 | 1 875 268 | 328 236 | refinada | segformer, manual | 39.6 | enu (métrico) | 0.5 | RTX A6000 |

## Casos que no llegaron a `metrics`

| Escena / experimento | Qué pasó | Lección |
|---|---|---|
| `utec_escaleras` v1 / v2 (1010 fotos, escalera interior) | v1: ~200 registradas en 11 fragmentos; v2 (SIFT afín + DSP): 654/1010 pero en 20 islas (la mayor de 86) | Concreto liso sin textura (~3 k features/foto): límite de la captura, no del pipeline |
| `sv_7c34` `streetview_single_v1` (36 vistas frontales) | 4/36 registradas, 32/630 pares con ≥15 inliers; densificación vacía | Con una vista por panorama y 10 m entre panoramas, casi ningún punto se ve desde 3 posiciones |
| `hornero_full_v2b` | Directorio clonado de `v2` con su `metrics.json`; sin corrida propia verificable | Al clonar un experimento, borrar `metrics/` |
| `sv_7c34_three` primera corrida (job 56800) | 43/92 en 2 modelos (p004–p010, p015–p023) con umbral de 20 inliers; `RefineMesh` colgado > 1 h → TIMEOUT | Los cortes no eran esquinas ni fechas: pares vecinos con 100–250 inliers pero sin puntos vistos desde 3 posiciones. Con 12 inliers (job 57023): 33 en el modelo principal |

## Lecturas por escena

- **barranco_drone** (video DJI 4K, 393 frames): 100 % de registro; `resolution_level 0` cuadruplica los puntos densos (84 M) sin mejorar la malla final. Referencia de validación del pipeline.
- **barranco_phone** (534 fotos): las máscaras SegFormer suben el registro de 87.5 % a 99.8 % y bajan un 15 % las caras (menos autos y vegetación). Ablación más clara del efecto del enmascaramiento.
- **barranco_full** (dron + teléfono, 927 fotos): 99.8–99.9 % con `vocab_tree`; SAM 3 añade poco en % enmascarado pero recorta la malla (5.5 M frente a 7.1 M caras).
- **hornero** (77 dron 36 MP + 148 teléfono 8 MP reexportado): v1 68 % en dos modelos; v2 con SIFT afín + DSP, guided matching y mapper permisivo llega al 100 % en un modelo; v3 con máscaras por fuente (SegFormer alucina en vista aérea). La mejor reconstrucción de la casa sigue siendo solo dron: el teléfono reexportado (sin EXIF, sobreexpuesto) contradice al dron en el denso.
- **hornero_drone** v1→v4: georef ENU + ROI (v2), SAM 3 en mosaico 3×2 para cables (v3, de 10.7 % a 13.2 % enmascarado) y `number-views-fuse 4` + `remove-spurious 60` (v4, 4.9 M puntos densos frente a 7.0 M). v3/v4 = versión final del dron; los cables residuales son artefactos de textura.
- **manzana_01/02/03** (captura a pie con intervalo 1 s, 466–632 fotos): 96.7–100 % de registro. El GPS de la app de cámara resultó inservible (posiciones cacheadas), de ahí `georef: plane` (modelo enderezado, no métrico). `manzana_03` incorpora la etapa `clean` (110 → 1 componente, 2.8 M → 1.4 M caras), el relleno del atlas y el detector `wires` (3.5 % de píxeles frente a 1.3 % de SAM 3 en cables).
- **sv_manzana_a/b** (Street View, 6 vistas por panorama, FOV 80°): 73 % y 56 % de registro en 3 y 5 fragmentos; mallas inservibles. **sv_7c34_three** (3 vistas por panorama orientadas a los puntos medios, FOV 110°): el emparejamiento entre vecinos funciona (100–250 inliers) pero el registro incremental se corta por falta de puntos vistos desde tres posiciones. Siguiente paso: SfM global (GLOMAP), rigs por panorama y descriptores aprendidos (`docs/plan-colmap4-features.md`).

## Cómo se generó

```bash
# en Khipu
python3 -m pipeline report --output outputs/experiments_summary.csv     # CSV plano con todas las claves
tar czf ~/metricas_experimentos.tgz outputs/experiments_summary.csv outputs/*/*/metrics/*.json
```

La tabla de arriba se construye a partir de los `metrics.json` seleccionando las
columnas de interés (registro, puntos, caras, máscaras, georef, tiempo, GPU);
los casos sin `metrics.json` se añaden a mano con los datos del log de SLURM.
