# =============================================================================
# Imagen Docker EQUIVALENTE a base.def + photogrammetry.def, para ejecutar el
# pipeline FUERA de Khipu (laptop/PC con Docker). Mismas versiones:
#   CUDA 12.6 / Ubuntu 22.04 + ffmpeg + Ceres 2.2.0 + COLMAP 3.11.1 (CUDA)
#   + OpenMVS 2.3.0 (CUDA) + Python 3 (PyYAML, numpy, PIL).
#
# Khipu NO usa este archivo: allí se construyen los .sif con Apptainer. Si se
# cambia algo aquí, cambiar también los .def (y viceversa).
#
# Construir (una vez, ~1-2 h según CPU; necesita ~15 GB de disco libre):
#   docker build -t photogrammetry:local -f containers/Dockerfile .
#
# CUDA_ARCHS: arquitecturas de GPU para las que se compila (por defecto cubre
# GeForce GTX 10xx=61, GTX 16xx/RTX 20xx/T4=75, RTX 30xx/A6000=86, RTX 40xx=89,
# A100=80). Para compilar más rápido, dejar solo la de tu GPU:
#   docker build --build-arg CUDA_ARCHS="86" -t photogrammetry:local -f containers/Dockerfile .
# (la de tu GPU: https://developer.nvidia.com/cuda-gpus, "Compute Capability" sin el punto)
#
# Ejecutar: ./containers/docker-run.sh ...   (ver docs/ejecutar-fuera-de-khipu.md)
# =============================================================================
FROM nvidia/cuda:12.6.3-devel-ubuntu22.04

ARG CUDA_ARCHS="61;75;80;86;89"
ARG J=8
ARG COLMAP_VERSION=3.11.1
ENV DEBIAN_FRONTEND=noninteractive LC_ALL=C

# ------------------------------------------------------------ dependencias apt
RUN apt-get update && apt-get install -y --no-install-recommends \
        git wget ca-certificates \
        build-essential cmake ninja-build pkg-config \
        ffmpeg \
        python3 python3-yaml python3-numpy python3-pil \
        libboost-filesystem-dev libboost-graph-dev libboost-iostreams-dev \
        libboost-program-options-dev libboost-serialization-dev libboost-system-dev \
        libeigen3-dev libflann-dev libfreeimage-dev libmetis-dev \
        libgoogle-glog-dev libgflags-dev libsqlite3-dev libglew-dev \
        libcgal-dev libsuitesparse-dev libatlas-base-dev \
        libopencv-dev libcurl4-openssl-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# -------------------------------------------------------------------- Ceres
RUN git clone --branch 2.2.0 --depth 1 https://github.com/ceres-solver/ceres-solver.git /opt/src/ceres-solver \
    && cmake -S /opt/src/ceres-solver -B /opt/src/ceres-solver/build -GNinja \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_TESTING=OFF -DBUILD_EXAMPLES=OFF -DBUILD_BENCHMARKS=OFF \
    && ninja -C /opt/src/ceres-solver/build -j${J} install \
    && rm -rf /opt/src/ceres-solver

# ------------------------------------------------------------------- COLMAP
RUN git clone --branch ${COLMAP_VERSION} --depth 1 https://github.com/colmap/colmap.git /opt/src/colmap \
    && cmake -S /opt/src/colmap -B /opt/src/colmap/build -GNinja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCUDA_ENABLED=ON \
        -DGUI_ENABLED=OFF \
        -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHS}" \
    && ninja -C /opt/src/colmap/build -j${J} install \
    && rm -rf /opt/src/colmap

# ------------------------------------------------------------------ OpenMVS
# CUDA_CUDA_LIBRARY -> stub: la máquina de build no necesita driver NVIDIA;
# en runtime `docker run --gpus all` inyecta la libcuda.so.1 real.
RUN git clone --depth 1 https://github.com/cdcseacave/VCG.git /opt/vcglib \
    && git clone --branch v2.3.0 --depth 1 --recursive https://github.com/cdcseacave/openMVS.git /opt/src/openMVS \
    && cmake -S /opt/src/openMVS -B /opt/src/openMVS/build -GNinja \
        -DCMAKE_BUILD_TYPE=Release \
        -DOpenMVS_USE_CUDA=ON \
        -DOpenMVS_USE_PYTHON=OFF \
        -DVCG_ROOT=/opt/vcglib \
        -DCUDA_CUDA_LIBRARY=/usr/local/cuda/lib64/stubs/libcuda.so \
        -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHS}" \
    && ninja -C /opt/src/openMVS/build -j${J} install \
    && rm -rf /opt/src /opt/vcglib

ENV PATH=/usr/local/bin/OpenMVS:$PATH

# Comprobación: falla el build si falta alguna pieza
RUN command -v ffmpeg && command -v ffprobe && command -v colmap \
    && python3 -c "import yaml, numpy, PIL" \
    && test -x /usr/local/bin/OpenMVS/DensifyPointCloud \
    && test -x /usr/local/bin/OpenMVS/ReconstructMesh \
    && test -x /usr/local/bin/OpenMVS/RefineMesh \
    && test -x /usr/local/bin/OpenMVS/TextureMesh \
    && test -x /usr/local/bin/OpenMVS/InterfaceCOLMAP \
    && echo "OK: ffmpeg + COLMAP + OpenMVS + Python listos"

# El repo se monta en /work (docker-run.sh); el pipeline se invoca con python3 -m pipeline
WORKDIR /work
CMD ["python3", "-m", "pipeline", "--help"]
