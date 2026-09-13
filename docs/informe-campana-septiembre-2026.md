# Pipeline de Fotogrametría para Khipu — Informe de campaña

**Proyecto:** Reconstrucción 3D del distrito de Barranco (PFC-II, UTEC)
**Período:** 1–11 de septiembre de 2026
**Infraestructura:** Cluster Khipu (SLURM + Apptainer; GPUs T4 / RTX A6000 / A100)

---

## 1. Resumen ejecutivo

En diez días se construyó y validó un pipeline reproducible de fotogrametría
(video/fotos → COLMAP → OpenMVS) y se ejecutó una campaña de **seis
experimentos** sobre dos cuadras del malecón de Barranco, capturadas con dron
(DJI, video 4K60) y teléfono (POCO X6, 534 fotos de 16 MP con GPS).

Los tres resultados centrales:

1. **Fusión dron + teléfono**: el matching por vocabulario visual (vocab_tree)
   une ambas fuentes en un solo modelo con **99.9 % de imágenes registradas**
   (926/927), combinando la cobertura aérea del dron con el detalle a nivel de
   calle del teléfono.
2. **Enmascaramiento semántico**: excluir personas, vehículos, vegetación y
   cielo de la reconstrucción subió el registro de fotos a pie de **87.4 % a
   99.8 %**, eliminó la fragmentación del modelo, redujo 15 % la geometría de
   la malla (basura) y aceleró el SfM 21 % — a un costo de ~7 minutos de GPU.
3. **Punto óptimo de calidad**: densificar a resolución completa produce 3.7×
   más puntos con 3.6× más tiempo, pero la malla final resulta visualmente
   indistinguible → media resolución (`resolution_level: 1`) es el estándar
   del proyecto.

---

## 2. Arquitectura del pipeline

### 2.1 Flujo de etapas

```
datasets/raw/<escena>/           outputs/<escena>/<experimento>/
   drone/*.MP4  ─┐
   phone/*.jpg  ─┤
                 ▼
 [frames]    ffmpeg extrae frames del video (fps, resize) y copia
             las fotos preservando EXIF (focal y GPS para COLMAP)
                 ▼
 [telemetry] parsea .srt de DJI a CSV (lat/lon/alt)          (opcional)
                 ▼
 [masks]     segmentación semántica → 1 máscara PNG por frame (opcional)
                 ▼
 [sfm]       COLMAP: features (SIFT-GPU) → matching → mapper (sparse)
                 ▼
 [undistort] COLMAP: imágenes sin distorsión (entrada del denso)
                 ▼
 [dense]     OpenMVS DensifyPointCloud → nube densa (.ply)
                 ▼
 [mesh]      OpenMVS ReconstructMesh (Delaunay+graph-cut con visibilidad)
             + RefineMesh (esculpido fotométrico)
                 ▼
 [texture]   OpenMVS TextureMesh → malla texturizada (.obj + atlas)
                 ▼
 [metrics]   métricas consolidadas (metrics.json, timings, hardware)
```

Principios de diseño:

- **Configuración declarativa**: cada experimento es un YAML que se combina
  (merge profundo) con `configs/default.yaml`. Cada herramienta expone sus
  parámetros comunes más un `extra_args` para cualquier flag adicional. La
  configuración exacta usada queda guardada en `config_resolved.yaml`.
- **Etapas reanudables**: cada etapa deja un marcador `.done` con su duración,
  nodo y job de SLURM. Si un job muere (tiempo, memoria), relanzar el mismo
  comando continúa desde el fallo. Varios experimentos de la campaña corrieron
  repartidos entre 2–3 nodos distintos sin intervención manual.
- **Multi-fuente nativo**: cada subcarpeta de la escena (drone/, phone/) se
  modela en COLMAP como una cámara independiente (`single_camera_per_folder`),
  lo correcto al mezclar dispositivos.

### 2.2 Contenedores (Apptainer)

| Imagen | Contenido | Rol |
|---|---|---|
| `base.sif` | CUDA 12.6 + FFmpeg + Ceres 2.2 + COLMAP 3.11.1 | etapas frames/sfm/undistort |
| `photogrammetry.sif` | base + OpenMVS 2.3.0 (CUDA) | pipeline completo |
| `segmentation.sif` | PyTorch 2.4 + transformers + SegFormer | solo la etapa masks |

Todo se compila para las tres arquitecturas GPU de Khipu (7.5/8.0/8.6), de modo
que la misma imagen corre en T4, A6000 o A100. La construcción es en dos etapas
para que iterar sobre OpenMVS no recompile COLMAP (~20 min vs ~1.5 h). Los
pesos del modelo de segmentación se descargan una vez en el nodo maestro (el
único con internet) y los nodos de cómputo los leen de la caché compartida en
modo offline.

### 2.3 Ejecución en SLURM

`slurm/submit.sh <config> <escena>` envía el job. El sbatch detecta si el
experimento usa enmascaramiento y, en ese caso, ejecuta primero la fase de
máscaras en `segmentation.sif` y luego el run principal en
`photogrammetry.sif`; los marcadores hacen el empalme transparente. Los
recursos se ajustan por variable (`SBATCH_OPTS`), con GPU compartida por
shards (`--gres=shard:N`).

---

## 3. El enmascaramiento semántico

### 3.1 Qué hace

Para cada frame se genera una máscara PNG del mismo tamaño donde **negro (0) =
píxel a ignorar** y blanco (255) = píxel útil. Las máscaras se aplican en dos
niveles:

- **Sparse (COLMAP)** — `--ImageReader.mask_path`: no se extraen features
  sobre los objetos enmascarados, así que personas, autos y hojas dejan de
  participar en el matching y en el bundle adjustment.
- **Denso (OpenMVS)** — `--mask-path` + `--ignore-mask-label 0`: esos píxeles
  tampoco se densifican, de modo que árboles y cielo no generan puntos ni
  geometría.

Clases enmascaradas (vocabulario Cityscapes): dinámicos (person, rider, car,
truck, bus, motorcycle, bicycle), vegetation, sky, y desde el último
experimento pole, traffic_light y traffic_sign. Cada máscara se dilata 15 px
para cubrir bordes imprecisos de la segmentación (y absorber el desplazamiento
de la undistorsión en el denso).

### 3.2 Cómo está construido

- **Backend intercambiable ("pieza de lego")**: la segmentación vive en
  `pipeline/mask_backends/` con un contrato de tres miembros
  (`SUPPORTED_CLASSES`, `is_available()`, `generate()`). El backend actual es
  **SegFormer-B5 fine-tuned en Cityscapes** (clases urbanas exactas para
  calle peruana). Cambiar de modelo = escribir un módulo con ese contrato y
  cambiar una línea de YAML; el resto del pipeline no se toca.
- **Opt-in estricto**: `masking.enabled: false` por defecto — sin activarlo,
  el pipeline es idéntico al flujo original (verificado con tests de
  regresión).
- **Etapa autónoma**: las máscaras pueden generarse e inspeccionarse solas
  (`--stages masks`), y son PNGs editables a mano si un caso puntual lo
  requiere (editar después de generar; no re-ejecutar la etapa o se
  sobreescriben).
- **Doble convención de nombres** (aprendida empíricamente): COLMAP espera
  `masks/<fuente>/<imagen>.png` espejando la estructura; OpenMVS espera
  `masks/<stem>.mask.png` plano en la raíz. Se generan ambas como hardlinks
  (cero espacio extra).

### 3.3 Qué no resuelve (honestidad técnica)

- **Cables**: no existen en los vocabularios estándar (2 px de grosor). Se
  mitigan con la máscara de postes + dilatación y con `remove-spurious` en la
  malla. Candidato futuro: backend SAM 3 (segmentación por texto libre).
- **Huecos**: enmascarar un árbol elimina su geometría pero no revela lo que
  tapaba — eso requiere más cobertura (fotos a pie bajo los árboles).
- **Sombras**: están horneadas en los píxeles de las fachadas; el remedio es
  de captura (día nublado), no de software.

---

## 4. La campaña de experimentos

| # | Experimento | Entrada | Registro | Error (px) | Nube densa | Malla refinada | Observación |
|---|---|---|---|---|---|---|---|
| 1 | `drone_v1` | video, 393 frames @2fps/1920 | 393/393 (100 %) | 0.751 | 22.5 M | 3.0 M caras | primera malla completa; ~2 h de cómputo |
| 2 | `drone_v2_dense0` | ídem, denso a resolución completa | 393/393 (100 %) | 0.751 | **84.0 M** | 2.26 M caras | 3.7× puntos, 3.6× tiempo, malla final ≈ igual |
| 3 | `phone_v1` | 534 fotos 16 MP | 467/534 (**87.4 %**) | 1.298 | 27.8 M | 9.6 M caras | modelo fragmentado en 2; matching sequential+spatial (GPS) |
| 4 | `phone_masked_v1` | ídem + máscaras | 533/534 (**99.8 %**) | 1.288 | 25.9 M | 8.2 M caras | 1 solo modelo; sfm −21 %; máscaras: 6.6 min, 31.5 % píxeles |
| 5 | `full_v1` | dron + teléfono (927 imgs) | 926/927 (**99.9 %**) | 1.032 | 54.6 M | 7.05 M caras | fusión vía vocab_tree; 2 cámaras, 1 modelo |
| 6 | `full_masked_v1` | ídem + máscaras (12 clases) | sparse: 918/927 (99.0 %) | — | en ejecución | en ejecución | el experimento de síntesis |

Notas de contexto operativo:

- Hardware por experimento registrado automáticamente en las métricas
  (`environment`, `stage_hosts`): la campaña usó A6000 (g002/ds001) y A100
  (ag001) según disponibilidad.
- Límite de la cuenta (`a-investigacion1`): 130 GB de RAM por usuario. El
  refinado de malla del experimento combinado excedió 128 GB (OOM) y se adaptó
  con una "dieta" (decimate 0.7, 1 escala, imágenes a ¼) que cabe en el
  límite; hay ticket de ampliación en Mesa de Ayuda.
- El desglose de tiempos por sub-etapa (features / cada matcher / mapper) se
  registra desde el experimento 5: p. ej. en `full_v1`, el vocab_tree costó
  30 min y el mapper 63 min de los 98 min de SfM.

---

## 5. Por qué los resultados mejoran

### 5.1 Por qué el enmascaramiento sube el registro (87 % → 99.8 %)

El matching secuencial de una caminata depende de que cada foto conecte con
sus vecinas. Personas y autos **se mueven entre fotos**: sus features generan
correspondencias inconsistentes que diluyen la evidencia geométrica; las hojas
de los árboles, aunque estáticas, son textura repetitiva e inestable que
produce matches ambiguos. En tramos dominados por vegetación, la cadena se
rompía — de ahí las 67 fotos perdidas y el modelo partido en dos del
experimento 3. Al extraer features **solo sobre estructura estable**
(fachadas, pistas, mobiliario), cada match aporta señal: la cadena no se
rompe, el modelo queda entero, y el bundle adjustment converge más rápido
(−21 % de tiempo con 66 imágenes más registradas).

### 5.2 Por qué "menos puntos" es mejor (27.8 M → 25.9 M)

La nube perdió 7 % de puntos quitando 31.5 % de los píxeles: lo eliminado era
geometría de árboles, cielo y autos — exactamente lo que ensuciaba la malla
(los "pegotes"). La malla refinada bajó de 9.6 M a 8.2 M caras conservando las
fachadas: se removió basura, no información.

### 5.3 Por qué la fusión dron + teléfono funciona

El matching secuencial no conecta fuentes distintas (ordena por nombre). El
**vocab_tree** reconoce lugares por apariencia, independientemente del orden:
las vistas aéreas del dron ven simultáneamente tramos que a pie están lejos en
la secuencia, actuando de "pegamento" — por eso el combinado registró 926/927
incluyendo fotos que el experimento de teléfono solo perdía. Las fuentes se
complementan: el dron aporta techos y coherencia global; el teléfono, fachadas
y los ángulos bajos que el dron no alcanza.

### 5.4 Por qué OpenMVS y no el denso de COLMAP

ReconstructMesh usa las **restricciones de visibilidad** (desde qué cámaras se
vio cada punto) para tallar el espacio vacío con un graph-cut — eso elimina de
raíz los "globos" que producen Poisson/Delaunay a ciegas. Además, la
implementación de OpenMVS del denso (PatchMatch) está optimizada para
producción: el denso del dron tomó 8 minutos donde el flujo COLMAP puro toma
horas (una referencia externa con el mismo video: ~7 h).

### 5.5 Por qué media resolución basta (experimento 2)

El refinado de malla re-esculpe y simplifica: partiendo de 22.5 M o de 84 M de
puntos, la malla final converge a ~1–2 M de vértices con detalle visual
equivalente. La resolución extra del denso se paga (28 min vs 8 min) pero no
se ve. Regla del proyecto: `resolution_level: 1`, y subir a 0 solo si un caso
concreto demuestra necesitarlo.

---

## 6. Lecciones de operación en Khipu

1. Construir imágenes **en tmux** (nohup no protege el `%post` de Apptainer
   del cierre de SSH).
2. Compilar sin GPU exige **stubs de CUDA** (`CUDA_CUDA_LIBRARY`); en runtime
   `--nv` inyecta el driver real.
3. **Sin `srun` dentro del sbatch** cuando se usan shards (los job steps no
   heredan gres de tipo shard).
4. Los shards conviene pedirlos **sin tipo** (`--gres=shard:8`) cuando
   cualquier GPU grande sirve: el filtro real lo hace `--mem`, y el job entra
   al primer nodo que se libere.
5. Conocer los **límites del QOS** (`sacctmgr show qos`): 130 GB RAM/usuario
   explica fallos que parecen misteriosos (jobs PD por `QOSMaxMemoryPerUser`).
6. Las convenciones de archivos de OpenMVS se verifican **empíricamente**
   (rutas de imágenes relativas al working folder; máscaras planas por stem).
7. Diseñar para el fallo: refinado no-fatal (si RefineMesh muere, se texturiza
   la malla base), etapas reanudables, mensajes de error con el comando de
   solución.

---

## 7. Próximos pasos

1. **Sesión de fotos de relleno**: bajo los árboles y en los huecos de
   cobertura, idealmente en día nublado (luz difusa, sin sombras horneadas).
   El pipeline las integra como una fuente más.
2. **Cables**: evaluar backend SAM 3 (prompts de texto) o modelos
   especializados (datasets tipo TTPLA); mientras tanto, postes enmascarados +
   `remove-spurious` + limpieza puntual de malla.
3. **Ampliación de RAM** (ticket en curso): re-refinar el combinado sin dieta
   (decimate 1.0, 2 escalas) y comparar.
4. **Geo-alineación**: usar el GPS del EXIF y los SRT del dron con
   `colmap model_aligner` para llevar cada reconstrucción a coordenadas mundo.
5. **Barranco por tiles**: con geo-alineación, reconstruir el distrito por
   cuadras (escenas del tamaño ya validado) que encajan en un mismo sistema de
   coordenadas.

---

*Documento generado a partir de las métricas registradas por el pipeline
(`outputs/*/*/metrics/metrics.json`, consolidadas con `python3 -m pipeline
report`). Repositorio: `photogrammetry-pipeline` — toda cifra citada es
reproducible desde los YAML de `configs/experiments/`.*
