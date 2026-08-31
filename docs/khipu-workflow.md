# Flujo de trabajo en Khipu

Guía paso a paso para ejecutar el pipeline en el cluster Khipu (UTEC). Asume el
alias `ssh khipu` ya configurado.

## 0. Contexto del cluster (resumen)

- **Nodo maestro** (`khipu`): único con internet. Aquí se clona el repo, se
  construye el contenedor y se envían jobs. **No** ejecutar cómputo pesado aquí.
- **Scheduler**: SLURM. Particiones relevantes:
  - `debug-gpu` (g001, máx. 30 min) — pruebas.
  - `gpu` (g001 Tesla T4 16 GB, g002 RTX A6000 48 GB, ag001 2×A100 40 GB) — producción.
- **GPU compartida por shards**: se pide con `--gres=shard:N` (recomendado por
  Khipu) o exclusiva con `--gres=gpu:1`. Tipos: `tesla`, `a100`, `rtxa6000`.
  Los shards no aíslan la memoria de GPU: dimensionar con criterio y ser
  considerado con los demás usuarios.
- **Apptainer** corre nativo en todos los nodos (sin módulos).

## 1. Clonar el repositorio (una vez)

```bash
ssh khipu
git clone <URL-del-repo> photogrammetry-pipeline
cd photogrammetry-pipeline
```

## 2. Construir el contenedor (una vez, ~1-2 h)

```bash
cd containers
nohup apptainer build photogrammetry.sif photogrammetry.def > build.log 2>&1 &
tail -f build.log        # Ctrl+C para salir; el build continúa
```

Al terminar:

```bash
apptainer test photogrammetry.sif
apptainer cache clean            # liberar la caché de blobs
cd ..
```

Verificación GPU (opcional pero recomendada):

```bash
srun -p debug-gpu --gres=shard:1 --mem=4G \
     apptainer exec --nv containers/photogrammetry.sif nvidia-smi
```

## 3. Subir los videos

Desde la laptop:

```bash
rsync -avP ./mi_escena/ khipu:~/photogrammetry-pipeline/datasets/raw/mi_escena/
```

Estructura esperada: ver [datasets/README.md](../datasets/README.md).

## 4. (Solo multi-fuente) Descargar el vocabulary tree

Necesario si el experimento usa `vocab_tree` o `loop_detection` (nodo maestro):

```bash
mkdir -p resources
wget -O resources/vocab_tree_flickr100K_words256K.bin \
     https://demuc.de/colmap/vocab_tree_flickr100K_words256K.bin
```

## 5. Configurar el experimento

```bash
cp configs/experiments/drone_2fps_1080p.yaml configs/experiments/mi_experimento.yaml
nano configs/experiments/mi_experimento.yaml   # cambiar experiment.name, fps, resize...
```

Validar sin gastar cómputo (corre en el nodo maestro, no usa GPU):

```bash
apptainer exec containers/photogrammetry.sif \
    python3 -m pipeline validate --config configs/experiments/mi_experimento.yaml --scene mi_escena
```

## 6. Smoke test (recomendado antes del run serio)

```bash
mkdir -p slurm-logs
sbatch -p debug-gpu --time=00:30:00 --gres=shard:4 --mem=16G \
       slurm/pipeline.sbatch configs/experiments/quick_test.yaml mi_escena
```

## 7. Ejecutar el experimento

```bash
./slurm/submit.sh configs/experiments/mi_experimento.yaml mi_escena
```

Con recursos personalizados:

```bash
SBATCH_OPTS="--gres=shard:rtxa6000:16 --mem=128G --time=24:00:00" \
  ./slurm/submit.sh configs/experiments/mi_experimento.yaml mi_escena
```

Guía rápida de dimensionamiento:

| Escenario | Sugerencia |
|---|---|
| Prueba corta / baja resolución | `debug-gpu`, `shard:4`, 16G RAM |
| 1080p, cientos de frames | `gpu`, `shard:8`, 64G RAM |
| 4K o miles de frames | `gpu`, `shard:rtxa6000:16` o `shard:a100:16`, 128G RAM |

La T4 (16 GB) se queda corta para PatchMatch/Densify a resolución alta: para
denso en 4K preferir `rtxa6000` o `a100`, o subir `resolution_level`.

## 8. Monitorear

```bash
squeue -u $USER                          # estado del job
tail -f slurm-logs/photogram-<jobid>.out # salida en vivo (todas las etapas)
tail -f outputs/mi_escena/<exp>/logs/dense.log   # log de una etapa concreta
scancel <jobid>                          # cancelar
```

## 9. Reanudar / re-ejecutar

Las etapas completadas dejan marcador en `outputs/.../.stages/`. Si el job
murió (tiempo, OOM), relanzar el mismo comando: **continúa donde quedó**.

```bash
# Re-ejecutar desde la densificación (p.ej. tras cambiar parámetros de OpenMVS)
./slurm/submit.sh configs/experiments/mi_experimento.yaml mi_escena --from-stage dense --force

# Solo repetir la extracción de frames
./slurm/submit.sh configs/experiments/mi_experimento.yaml mi_escena --stages frames --force
```

Ojo: cambiar parámetros de una etapa temprana (p.ej. `fps`) invalida las
posteriores; en ese caso preferible cambiar `experiment.name` para escribir en
una carpeta nueva y conservar el run anterior para comparar.

## 10. Resultados y descarga

```bash
# Consolidar métricas de todos los experimentos
apptainer exec containers/photogrammetry.sif python3 -m pipeline report
```

Desde la laptop:

```bash
# Solo malla + métricas (ligero)
rsync -avP khipu:~/photogrammetry-pipeline/outputs/mi_escena/mi_experimento/mvs/scene_texture.* ./
rsync -avP khipu:~/photogrammetry-pipeline/outputs/mi_escena/mi_experimento/metrics/ ./metrics/

# Todo el experimento (pesado)
rsync -avP khipu:~/photogrammetry-pipeline/outputs/mi_escena/mi_experimento/ ./mi_experimento/
```

Visualizar `.ply`/`.obj` localmente con **MeshLab** o **CloudCompare**.

## Problemas frecuentes

| Síntoma | Causa probable / solución |
|---|---|
| `no existe la imagen containers/photogrammetry.sif` | Construir el contenedor (paso 2) o exportar `CONTAINER=/ruta/al/sif` |
| Job muere en `dense` por memoria GPU | Subir `openmvs.densify.resolution_level`, pedir GPU más grande, o reducir resolución de frames |
| Pocas imágenes registradas en sparse | Subir `fps`, subir `sequential_overlap`, revisar frames borrosos, probar `loop_detection` |
| El mapper produce varios modelos desconectados | Falta overlap entre videos/fuentes: añadir `vocab_tree` al matching |
| `Permission denied` al ejecutar `submit.sh` | `chmod +x slurm/submit.sh` (o `bash slurm/submit.sh ...`) |
| Job en cola mucho tiempo | Reducir recursos pedidos, o probar otro tipo de GPU |
