# =============================================================================
# Imagen Docker EQUIVALENTE a base.def + photogrammetry.def, para ejecutar el
# pipeline FUERA de Khipu (laptop/PC con Docker). Mismas versiones:
#   CUDA 12.8 / Ubuntu 22.04 + ffmpeg + Ceres 2.2.0 + COLMAP 3.11.1 (CUDA)
#   + OpenMVS 2.3.0 (CUDA) + Python 3 (PyYAML, numpy, PIL).
#
# Khipu NO usa este archivo: allí se construyen los .sif con Apptainer. Si se
# cambia algo aquí, cambiar también los .def (y viceversa).
#
# Construir (una vez, ~1-2 h según CPU; necesita ~15 GB de disco libre):
#   docker build -t photogrammetry:local -f containers/Dockerfile .
#
# CUDA_ARCHS: por defecto RTX 30xx/A6000=86, RTX 40xx=89 y Blackwell L507=120.
# Para compilar más rápido en el laboratorio, dejar solo la de la GPU:
#   docker build --build-arg CUDA_ARCHS="120" -t photogrammetry:local -f containers/Dockerfile .
# (la de tu GPU: https://developer.nvidia.com/cuda-gpus, "Compute Capability" sin el punto)
#
# Ejecutar: ./containers/docker-run.sh ...   (ver docs/ejecutar-fuera-de-khipu.md)
# =============================================================================
FROM nvidia/cuda:12.8.1-devel-ubuntu22.04

ARG CUDA_ARCHS="86;89;120"
ARG J=8
ENV DEBIAN_FRONTEND=noninteractive LC_ALL=C

# ------------------------------------------------------------ dependencias apt
RUN apt-get update && apt-get install -y --no-install-recommends \
        git wget ca-certificates \
        build-essential cmake ninja-build pkg-config \
        ffmpeg \
        python3 python3-pip python3-dev python3-yaml python3-numpy python3-pil \
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
RUN git clone --branch 3.11.1 --depth 1 https://github.com/colmap/colmap.git /opt/src/colmap \
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
    && sed -i "s/CUDA_ARCHITECTURES \"50;72;75\"/CUDA_ARCHITECTURES \"${CUDA_ARCHS}\"/g" /opt/src/openMVS/libs/MVS/CMakeLists.txt \
    && ! grep -q '50;72;75' /opt/src/openMVS/libs/MVS/CMakeLists.txt \
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

# Keep Python bindings on exactly the COLMAP source revision used by the CLI.
RUN python3 -m pip install --no-cache-dir pip==25.3 \
    && python3 -m pip install --no-cache-dir scikit-build-core==0.11.6 pybind11==2.13.6 \
        numpy==1.26.4 PyYAML==6.0.2 Pillow==10.4.0 \
    && git clone --branch 3.11.1 --depth 1 https://github.com/colmap/colmap.git /opt/pycolmap-src \
    && test "$(git -C /opt/pycolmap-src rev-parse HEAD)" = "682ea9ac4020a143047758739259b3ff04dabe8d" \
    && CMAKE_BUILD_PARALLEL_LEVEL=${J} python3 -m pip install --no-cache-dir --no-build-isolation --no-deps \
       --config-settings=cmake.define.GENERATE_STUBS=OFF /opt/pycolmap-src/pycolmap \
    && rm -rf /opt/pycolmap-src && ldconfig \
    && python3 -c "import pycolmap; assert pycolmap.has_cuda; print(pycolmap.__version__)"

LABEL photogrammetry.cuda_archs="${CUDA_ARCHS}"

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
