"""Etapa 'masks': segmentación semántica de los frames para excluir obstáculos.

Produce en outputs/<escena>/<exp>/masks/<fuente>/ una máscara por frame:
  - <imagen>.png       (convención COLMAP: --ImageReader.mask_path)
  - <imagen>.mask.png  (hardlink, convención OpenMVS: --mask-path)
donde 0 = ignorar y 255 = usar.

La etapa es autónoma y tolerante al contenedor:
  - En el contenedor de segmentación (torch disponible) genera las máscaras.
  - En el contenedor de fotogrametría, si las máscaras ya están completas las
    valida y reutiliza; si faltan, falla con el comando exacto para generarlas.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .config import Context
from .executor import CommandError
from . import mask_backends


def colmap_mask_path(image: Path) -> str:
    return image.name + ".png"


def openmvs_mask_path(image: Path) -> str:
    return image.name + ".mask.png"


def frame_images(frames_dir: Path) -> dict[str, list[Path]]:
    """Imágenes de frames por fuente (subcarpeta)."""
    sources: dict[str, list[Path]] = {}
    if not frames_dir.is_dir():
        return sources
    for source_dir in sorted(p for p in frames_dir.iterdir() if p.is_dir()):
        images = sorted(p for p in source_dir.iterdir() if p.is_file())
        if images:
            sources[source_dir.name] = images
    return sources


def masks_complete(ctx: Context) -> bool:
    """True si existe una máscara COLMAP por cada frame."""
    sources = frame_images(ctx.frames_dir)
    if not sources:
        return False
    for source, images in sources.items():
        mask_dir = ctx.masks_dir / source
        for image in images:
            if not (mask_dir / colmap_mask_path(image)).is_file():
                return False
    return True


def require_masks(ctx: Context) -> None:
    """Usado por sfm/dense cuando masking.enabled: exige máscaras completas."""
    if not masks_complete(ctx):
        raise CommandError(
            "masking.enabled=true pero faltan máscaras. Generarlas con:\n"
            "  apptainer exec --nv containers/segmentation.sif \\\n"
            f"      python3 -m pipeline run --config <cfg> --scene {ctx.scene} --stages masks"
        )


def _link_openmvs_aliases(mask_dir: Path, images: list[Path]) -> None:
    """Crea <imagen>.mask.png como hardlink de <imagen>.png (cero espacio extra)."""
    for image in images:
        src = mask_dir / colmap_mask_path(image)
        dst = mask_dir / openmvs_mask_path(image)
        dst.unlink(missing_ok=True)
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)


def run_masks(ctx: Context) -> None:
    mcfg = ctx.cfg.get("masking") or {}
    if not mcfg.get("enabled"):
        print("[masks] masking.enabled = false: etapa omitida")
        return

    sources = frame_images(ctx.frames_dir)
    if not sources:
        raise CommandError(f"No hay frames en {ctx.frames_dir}: ejecutar antes la etapa 'frames'.")

    backend_name = mcfg.get("backend", "segformer")
    backend = mask_backends.get_backend(backend_name)  # backend desconocido = error siempre

    # Validar clases sin necesidad de torch
    if hasattr(backend, "resolve_class_ids"):
        backend.resolve_class_ids(mcfg.get("classes") or [])

    if not backend.is_available():
        if masks_complete(ctx):
            print(f"[masks] backend '{backend_name}' no disponible en este contenedor; "
                  "las máscaras existentes están completas y se reutilizan.")
            return
        raise CommandError(
            f"El backend '{backend_name}' necesita el contenedor de segmentación "
            "(torch no está disponible aquí) y no hay máscaras completas. Generarlas con:\n"
            "  apptainer exec --nv containers/segmentation.sif \\\n"
            f"      python3 -m pipeline run --config <cfg> --scene {ctx.scene} --stages masks"
        )

    # Generación limpia
    if ctx.masks_dir.exists():
        shutil.rmtree(ctx.masks_dir)

    info: dict = {"backend": backend_name, "classes": mcfg.get("classes"),
                  "dilate_px": mcfg.get("dilate_px"), "sources": {}}
    for source, images in sources.items():
        mask_dir = ctx.masks_dir / source
        mask_dir.mkdir(parents=True, exist_ok=True)
        results = backend.generate(images, mask_dir, mcfg)
        _link_openmvs_aliases(mask_dir, images)

        ratios = [r["masked_ratio"] for r in results]
        mean_ratio = round(sum(ratios) / len(ratios), 4) if ratios else 0.0
        heavily_masked = [r["image"] for r in results if r["masked_ratio"] > 0.8]
        info["sources"][source] = {
            "images": len(results),
            "mean_masked_ratio": mean_ratio,
            "heavily_masked": heavily_masked,
        }
        print(f"[masks] {source}: {len(results)} máscaras, "
              f"{mean_ratio:.1%} de píxeles enmascarados en promedio")
        if heavily_masked:
            print(f"[masks] AVISO: {len(heavily_masked)} imágenes con >80% enmascarado "
                  "(quedará poca señal útil en ellas)")

    ctx.metrics_dir.mkdir(parents=True, exist_ok=True)
    with open(ctx.metrics_dir / "masks_info.json", "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2, ensure_ascii=False)
