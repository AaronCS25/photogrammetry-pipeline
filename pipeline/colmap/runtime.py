"""Fail closed on untested COLMAP versions; translate general flags centrally."""
import re
import subprocess
import shutil

from ..config import ConfigError


def version() -> str:
    p = subprocess.run(["colmap", "-h"], capture_output=True, text=True)
    m = re.search(r"COLMAP\s+(\d+\.\d+(?:\.\d+)?)", p.stdout + p.stderr)
    if not m:
        raise ConfigError("No se pudo identificar COLMAP")
    return m.group(1)


def require_version(expected: str) -> str:
    actual = version()
    if actual != expected:
        raise ConfigError(f"Se requiere COLMAP {expected}; disponible {actual}. No se modifica ninguna BD.")
    return actual


def general_flags(section: str, threads: int, gpu: bool) -> list[str]:
    return [f"--Feature{section}.num_threads", str(threads),
            f"--Feature{section}.use_gpu", str(int(gpu))]


def capabilities(expected: str, target: str = "sparse") -> dict:
    """Help/version probes only; this does not perform reconstruction."""
    actual = require_version(expected)
    required = {"feature_extractor": ("FeatureExtraction.use_gpu", "FeatureExtraction.num_threads"),
                "matches_importer": ("match_list_path", "FeatureMatching.use_gpu"),
                "mapper": ("input_path", "Mapper.num_threads"),
                "image_undistorter": ("input_path", "output_path")}
    for command, flags in required.items():
        p = subprocess.run(["colmap", command, "-h"], capture_output=True, text=True)
        if any("--" + f not in p.stdout + p.stderr for f in flags):
            raise ConfigError(f"CLI COLMAP incompatible: {command}")
    binaries = ["ffmpeg", "ffprobe"]
    if target != "sparse":
        binaries += ["InterfaceCOLMAP", "DensifyPointCloud"]
    if target in ("mesh", "textured"):
        binaries += ["ReconstructMesh", "RefineMesh"]
    if target == "textured":
        binaries += ["TextureMesh"]
    paths = {b: shutil.which(b) for b in binaries}
    if not all(paths.values()):
        raise ConfigError(f"Faltan binarios: {paths}")
    return {"colmap_version": actual, "binaries": paths,
            "note": "Verificación de CLI, no certifica reconstrucción/OpenMVS end-to-end"}
