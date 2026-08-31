# Datasets

Colocar los datos crudos en `datasets/raw/<nombre-de-escena>/`. El nombre de la
escena identifica al conjunto en `outputs/` y en las métricas: usar nombres
descriptivos sin espacios (ej. `plaza_utec_v1`, `fachada_norte_2026-08`).

## Estructura esperada

### Escena con una sola fuente (solo dron o solo teléfono)

Los videos pueden ir directamente en la carpeta de la escena; se tratan como la
fuente `main`:

```
datasets/raw/plaza_utec_v1/
├── DJI_0001.MP4
├── DJI_0001.SRT        # opcional: telemetría DJI, se parsea a CSV
└── DJI_0002.MP4
```

### Escena multi-fuente (dron + teléfono)

Una subcarpeta por dispositivo. Cada subcarpeta se modela en COLMAP como una
cámara independiente (intrínsecos propios), que es lo correcto al mezclar
dispositivos:

```
datasets/raw/fachada_norte/
├── drone/
│   ├── DJI_0010.MP4
│   └── DJI_0010.SRT
└── phone/
    ├── VID_20260830_101502.mp4
    └── VID_20260830_101745.mp4
```

> Con multi-fuente, usar un experimento con matching `vocab_tree` además de
> `sequential` (ver `configs/experiments/multisource_drone_phone.yaml`), porque
> el matching secuencial no conecta videos distintos entre sí.

## Formatos soportados

- **Video** (por ahora la única entrada): `.mp4 .mov .m4v .avi .mkv .mts .m2ts .webm`
  (mayúsculas o minúsculas).
- **Telemetría**: `.srt` de DJI con el mismo nombre base que su video.
- **Fotos sueltas**: aún no soportadas (extensión prevista).

## Subir datos a Khipu

Desde la laptop (con el alias `ssh khipu` configurado):

```bash
rsync -avP ./fachada_norte/ khipu:~/photogrammetry-pipeline/datasets/raw/fachada_norte/
```

`rsync` permite reanudar transferencias interrumpidas; para archivos sueltos
también sirve `scp -r`.

Esta carpeta está en `.gitignore`: los videos **nunca** se suben al repositorio.
