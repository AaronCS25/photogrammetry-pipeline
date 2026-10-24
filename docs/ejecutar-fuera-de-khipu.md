# Ejecutar el pipeline fuera de Khipu (laptop / PC)

El repositorio **se clona en segundos pero no corre "tal cual"**: el código
Python es la parte pequeña. El trabajo lo hacen **COLMAP**, **OpenMVS** y
**FFmpeg**, programas en C++ con CUDA que no se instalan con `pip`. En Khipu
van dentro de un contenedor Apptainer; en tu máquina la forma garantizada de
tener exactamente las mismas versiones es la imagen Docker de
`containers/Dockerfile`, que es una copia 1:1 de los `.def` del cluster.

Sigue los pasos **en orden** y no pases al siguiente hasta que la comprobación
de cada uno salga bien. Nada de esto toca el flujo de Khipu.

## 0. Qué necesitas

| | Mínimo | Comentario |
|---|---|---|
| Sistema | Ubuntu 22.04/24.04, o **Windows 11 con WSL2** (Ubuntu) | macOS **no** sirve: no hay CUDA |
| GPU | NVIDIA con driver instalado (GTX 10xx o superior) | Sin GPU solo llegas hasta `sfm`/`undistort` (paso 7) |
| Disco | ~30 GB libres | 15 GB la imagen Docker + datos + salidas |
| RAM | 16 GB | 32 GB para escenas de cientos de fotos |
| Tiempo | 1-2 h la primera vez | es la compilación de COLMAP y OpenMVS; solo una vez |

En **Windows** todo lo que sigue se escribe en la terminal de **Ubuntu (WSL2)**,
no en PowerShell. Instalar WSL2: en PowerShell como administrador,
`wsl --install -d Ubuntu`, reiniciar y abrir "Ubuntu" desde el menú Inicio.
El driver NVIDIA se instala **en Windows** (el normal de GeForce); dentro de
WSL no hay que instalar ningún driver.

## 1. Instalar Docker (y el soporte de GPU)

**Ubuntu nativo**

```bash
# Docker
sudo apt-get update && sudo apt-get install -y ca-certificates curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"      # para no usar sudo en cada comando
newgrp docker                        # (o cerrar sesión y volver a entrar)

# Soporte de GPU en contenedores (NVIDIA Container Toolkit)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

**Windows 11 + WSL2**: instalar **Docker Desktop** desde docker.com y, en
Settings → General, marcar "Use the WSL 2 based engine"; en Settings →
Resources → WSL integration, activar "Ubuntu". El soporte de GPU viene
incluido: no hay que instalar el toolkit.

**Comprobación** (todas deben funcionar antes de seguir):

```bash
docker run --rm hello-world                     # imprime "Hello from Docker!"
nvidia-smi                                      # lista tu GPU (si tienes)
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu22.04 nvidia-smi   # la misma tabla, desde Docker
```

Si `docker run` da `permission denied` → falta el `usermod`/`newgrp` de arriba.
Si la tercera da `could not select device driver "" with capabilities: [[gpu]]`
→ el NVIDIA Container Toolkit no está instalado o Docker no se reinició.

## 2. Clonar el repositorio

```bash
cd ~                                  # en WSL: dentro de Linux, NO en /mnt/c (10x más lento)
git clone <URL-del-repo> photogrammetry-pipeline
cd photogrammetry-pipeline
ls                                    # debe listar: configs containers docs pipeline slurm ...
```

## 3. Construir la imagen (una sola vez)

```bash
docker build -t photogrammetry:local -f containers/Dockerfile .
```

Tarda 1-2 h y termina con `OK: ffmpeg + COLMAP + OpenMVS + Python listos`.
Si quieres acortarlo, compila solo para tu GPU (ver cabecera del `Dockerfile`,
`--build-arg CUDA_ARCHS=...`).

**Comprobación:**

```bash
./containers/docker-run.sh colmap -h | head -3            # COLMAP 3.11.1 ...
./containers/docker-run.sh DensifyPointCloud --help | head -3
./containers/docker-run.sh python3 -m pipeline --help
```

`docker-run.sh` monta el repo en `/work` dentro del contenedor y pide la GPU
automáticamente si existe `nvidia-smi` en el host. Es el equivalente local de
`slurm/submit.sh`.

## 4. Colocar los datos

Igual que en Khipu (ver `datasets/README.md`): una carpeta por escena bajo
`datasets/raw/`, con los videos o las fotos dentro.

```bash
mkdir -p datasets/raw/mi_escena/phone
cp /ruta/a/mis/fotos/*.jpg datasets/raw/mi_escena/phone/
ls datasets/raw/mi_escena/phone | wc -l        # nº de fotos
```

Para una **primera prueba** usa pocas imágenes (30-80 fotos de una fachada,
o un video de 1-2 min) y el preset `configs/experiments/quick_test.yaml`:
corre de punta a punta en minutos.

## 5. Validar (sin gastar cómputo)

```bash
./containers/docker-run.sh python3 -m pipeline validate \
    --config configs/experiments/quick_test.yaml --scene mi_escena
```

Debe terminar con `✔ Configuración válida.` y, en "Binarios en PATH", las
cuatro herramientas con ruta (`/usr/bin/ffmpeg`, `/usr/local/bin/colmap`,
`/usr/local/bin/OpenMVS/DensifyPointCloud`...). Si alguna dice
`NO ENCONTRADO`, no estás dentro de la imagen: vuelve al paso 3.

## 6. Ejecutar

```bash
./containers/docker-run.sh python3 -m pipeline run \
    --config configs/experiments/quick_test.yaml --scene mi_escena
```

Las salidas quedan en `outputs/mi_escena/quick_test/` (frames, `colmap/`,
`mvs/`, `metrics/metrics.json`, `logs/`). Para reanudar tras un corte basta
repetir el mismo comando: las etapas terminadas se saltan. Después, copia
cualquier preset de `configs/experiments/` a uno tuyo y ajústalo
(`docs/configuration.md`).

Órdenes de magnitud en una GPU de portátil (RTX 3060): `quick_test` con 60
fotos, ~10 min; una manzana de 400 fotos con `manzana_phone.yaml` (sin
máscaras), 2-4 h.

## 7. Si no tienes GPU NVIDIA

Puedes correr frames, SfM (COLMAP con SIFT en CPU, lento), georef y
undistort. La densificación con OpenMVS está compilada con CUDA y **no está
probada sin GPU**; el backend denso de COLMAP la exige. Crea un preset:

```yaml
# configs/experiments/local_cpu.yaml
experiment: {name: local_cpu}
frames: {resize: {long_edge: 1280}}
runtime: {gpu: false}
```

y ejecuta solo las etapas que no necesitan GPU:

```bash
GPU=0 ./containers/docker-run.sh python3 -m pipeline run \
    --config configs/experiments/local_cpu.yaml --scene mi_escena \
    --stages frames,sfm,undistort
```

## 8. Lo que NO incluye la imagen

- **Máscaras semánticas** (`masking.enabled: true`, backends `segformer` y
  `sam3`): necesitan PyTorch con CUDA y los pesos de los modelos. En Khipu
  van en otro contenedor (`containers/segmentation.def`). Localmente deja
  `masking.enabled: false` (es el valor por defecto; `quick_test` no las usa).
  Los backends `wires` y `manual` sí funcionan con la imagen de este documento.
- **SLURM**: `slurm/submit.sh` y `slurm/pipeline.sbatch` son solo para Khipu.

## 9. Alternativa sin Docker (avanzado, solo Linux)

Si prefieres instalar todo nativo en Ubuntu 22.04, la receta exacta son los
pasos `%post` de `containers/base.def` y `containers/photogrammetry.def`
(apt + Ceres 2.2.0 + COLMAP 3.11.1 + OpenMVS 2.3.0 desde fuente, ~2 h). El
`colmap` de `apt install` es la versión 3.7 sin CUDA y le faltan opciones que
el pipeline usa: **no sirve**. Después:

```bash
python3 -m pip install -r requirements.txt       # pyyaml, numpy, pillow
export PATH=/usr/local/bin/OpenMVS:$PATH
python3 -m pipeline validate --config configs/experiments/quick_test.yaml --scene mi_escena
```

`validate` te dice qué binario falta. En Windows nativo (sin WSL) no hay
soporte: OpenMVS no publica binarios oficiales y el pipeline asume rutas POSIX.

## 10. Problemas frecuentes

| Síntoma | Causa / solución |
|---|---|
| `ModuleNotFoundError: No module named 'yaml'` o el aviso `falta el paquete Python 'PyYAML'` | Estás fuera del contenedor y sin dependencias: `python3 -m pip install -r requirements.txt`, o usa `docker-run.sh` |
| `validate` dice `colmap: NO ENCONTRADO` | Estás ejecutando con el Python del sistema, no dentro de la imagen. Usa `./containers/docker-run.sh python3 -m pipeline ...` |
| `docker: permission denied while trying to connect to the Docker daemon` | `sudo usermod -aG docker $USER` y volver a entrar |
| `could not select device driver "" with capabilities: [[gpu]]` | Falta NVIDIA Container Toolkit (paso 1) o reiniciar Docker; o ejecuta con `GPU=0` |
| `ERROR: la imagen 'photogrammetry:local' no existe` | Falta el paso 3 (`docker build`) |
| `CUDA error: no kernel image is available for execution on the device` | La imagen no se compiló para tu GPU: reconstruir con `--build-arg CUDA_ARCHS="<tu arquitectura>"` |
| Archivos en `outputs/` propiedad de `root` | Ocurre si se ejecutó `docker run` a mano sin `-u`; `docker-run.sh` ya lo evita. `sudo chown -R $USER outputs` |
| Muy lento en WSL2 | El repo está en `/mnt/c/...`; muévelo a `~/` dentro de Linux |
| Se queda sin memoria en `dense` | Subir `openmvs.densify.resolution_level` (2 = un cuarto) o usar menos fotos |
