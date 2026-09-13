"""Etapa 'masks': segmentación semántica de los frames para excluir obstáculos.

Produce en outputs/<escena>/<exp>/masks/ una máscara por frame:
  - <fuente>/<imagen>.png   (convención COLMAP: --ImageReader.mask_path)
  - <stem>.mask.png         (hardlink plano en la raíz, convención OpenMVS)
donde 0 = ignorar y 255 = usar.

Varios backends pueden ENCADENARSE (`masking.backend: [segformer, sam3, manual]`):
cada uno escribe sus máscaras en <fuente>/.<backend>/ y la etapa las fusiona
(un píxel se ignora si CUALQUIER backend lo ignora).

La etapa es autónoma y tolerante al contenedor:
  - En el contenedor de segmentación (dependencias disponibles) genera.
  - En otro contenedor, si las máscaras ya están completas las reutiliza; si
    faltan, falla con el comando exacto para generarlas.
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
    """COLMAP: <masks>/<fuente>/<imagen completa>.png (espeja la estructura)."""
    return image.name + ".png"


def openmvs_mask_path(image: Path) -> str:
    """OpenMVS (v2.3.0, comprobado empíricamente): busca PLANO en la raíz de
    --mask-path, con el stem de la imagen: <masks>/<stem>.mask.png."""
    return image.stem + ".mask.png"


def backend_names(masking_cfg: dict) -> list[str]:
    """`backend` acepta un nombre o una lista (cadena de backends)."""
    raw = masking_cfg.get("backend", "segformer")
    if isinstance(raw, str):
        names = [raw]
    elif isinstance(raw, (list, tuple)) and all(isinstance(n, str) for n in raw):
        names = list(raw)
    else:
        raise CommandError(
            f"masking.backend debe ser un nombre o una lista de nombres, no {raw!r}")
    if not names:
        raise CommandError("masking.backend está vacío: indicar al menos un backend")
    if len(set(names)) != len(names):
        raise CommandError(f"masking.backend tiene backends repetidos: {names}")
    return names


def _backend_cfg(masking_cfg: dict, backend_name: str) -> dict:
    return (masking_cfg.get("backends") or {}).get(backend_name) or {}


def effective_classes(masking_cfg: dict, backend_name: str) -> list[str]:
    """Clases de un backend: su bloque `backends.<nombre>.classes` si existe,
    si no las globales `masking.classes`."""
    return list(_backend_cfg(masking_cfg, backend_name).get("classes",
                                                             masking_cfg.get("classes")) or [])


def effective_dilate(masking_cfg: dict, backend_name: str) -> int:
    """Dilatación de un backend: su bloque si la define, si no la global."""
    return int(_backend_cfg(masking_cfg, backend_name).get("dilate_px",
                                                            masking_cfg.get("dilate_px", 15)))


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


def _link_openmvs_aliases(ctx: Context, sources: dict[str, list[Path]]) -> None:
    """Crea en la RAÍZ de masks/ los alias planos <stem>.mask.png que espera
    OpenMVS, como hardlinks de las máscaras COLMAP (cero espacio extra)."""
    seen: dict[str, str] = {}
    for source, images in sources.items():
        for image in images:
            alias = openmvs_mask_path(image)
            owner = f"{source}/{image.name}"
            if alias in seen:
                raise CommandError(
                    f"Colisión de máscaras OpenMVS: '{alias}' corresponde tanto a "
                    f"{seen[alias]} como a {owner}. Renombrar los archivos para que "
                    "los stems sean únicos entre fuentes."
                )
            seen[alias] = owner
            src = ctx.masks_dir / source / colmap_mask_path(image)
            dst = ctx.masks_dir / alias
            dst.unlink(missing_ok=True)
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)


def _fuse_masks(images: list[Path], partial_dirs: list[Path], mask_dir: Path) -> list[float]:
    """Fusiona las máscaras parciales de varios backends: se ignora (0) todo
    píxel que cualquier backend ignore. Devuelve el ratio enmascarado final."""
    import numpy as np
    from PIL import Image

    ratios = []
    for image in images:
        name = colmap_mask_path(image)
        fused = None
        for pdir in partial_dirs:
            with Image.open(pdir / name) as m:
                arr = np.array(m.convert("L"))
            fused = arr if fused is None else np.minimum(fused, arr)
        Image.fromarray(fused).save(mask_dir / name)
        ratios.append(round(float((fused == 0).mean()), 4))
    return ratios


def run_masks(ctx: Context) -> None:
    mcfg = ctx.cfg.get("masking") or {}
    if not mcfg.get("enabled"):
        print("[masks] masking.enabled = false: etapa omitida")
        return

    sources = frame_images(ctx.frames_dir)
    if not sources:
        raise CommandError(f"No hay frames en {ctx.frames_dir}: ejecutar antes la etapa 'frames'.")

    names = backend_names(mcfg)
    backends = [(n, mask_backends.get_backend(n)) for n in names]  # desconocido = error

    # Validaciones que no requieren dependencias pesadas
    for name, backend in backends:
        if hasattr(backend, "resolve_class_ids"):
            backend.resolve_class_ids(effective_classes(mcfg, name))

    missing = [n for n, b in backends if not b.is_available()]
    if missing:
        if masks_complete(ctx):
            # Reutilizar solo si se generaron con la MISMA cadena de backends
            info_file = ctx.metrics_dir / "masks_info.json"
            previous = None
            if info_file.is_file():
                try:
                    previous = json.loads(info_file.read_text(encoding="utf-8")).get("backends")
                except json.JSONDecodeError:
                    previous = None
            if previous is not None and list(previous) != names:
                raise CommandError(
                    f"Las máscaras existentes se generaron con backends {previous} pero la "
                    f"configuración pide {names}. Regenerarlas en el contenedor de segmentación:\n"
                    "  apptainer exec --nv containers/segmentation.sif \\\n"
                    f"      python3 -m pipeline run --config <cfg> --scene {ctx.scene} "
                    "--stages masks --force"
                )
            print(f"[masks] backends {missing} no disponibles en este contenedor; "
                  "las máscaras existentes están completas y se reutilizan.")
            return
        raise CommandError(
            f"Los backends {missing} necesitan el contenedor de segmentación "
            "(dependencias no disponibles aquí) y no hay máscaras completas. Generarlas con:\n"
            "  apptainer exec --nv containers/segmentation.sif \\\n"
            f"      python3 -m pipeline run --config <cfg> --scene {ctx.scene} --stages masks"
        )

    # Generación limpia
    if ctx.masks_dir.exists():
        shutil.rmtree(ctx.masks_dir)

    # Procedencia: la configuración EFECTIVA de cada backend (globales +
    # overrides de su bloque), para que las métricas describan lo que se hizo.
    info: dict = {
        "backends": names,
        "classes": mcfg.get("classes"),
        "dilate_px": mcfg.get("dilate_px"),
        "per_backend_config": {
            name: {
                "classes": effective_classes(mcfg, name) if backend.SUPPORTED_CLASSES else None,
                "dilate_px": effective_dilate(mcfg, name),
                **{k: v for k, v in _backend_cfg(mcfg, name).items()
                   if k not in ("classes", "dilate_px")},
            }
            for name, backend in backends
        },
        "sources": {},
    }
    for source, images in sources.items():
        mask_dir = ctx.masks_dir / source
        mask_dir.mkdir(parents=True, exist_ok=True)
        per_backend: dict[str, float] = {}

        if len(backends) == 1:
            name, backend = backends[0]
            results = backend.generate(images, mask_dir, mcfg, ctx)
            ratios = [r["masked_ratio"] for r in results]
            per_backend[name] = round(sum(ratios) / len(ratios), 4) if ratios else 0.0
        else:
            partial_dirs = []
            for name, backend in backends:
                pdir = mask_dir / f".{name}"
                pdir.mkdir(exist_ok=True)
                results = backend.generate(images, pdir, mcfg, ctx)
                r = [x["masked_ratio"] for x in results]
                per_backend[name] = round(sum(r) / len(r), 4) if r else 0.0
                partial_dirs.append(pdir)
            print(f"[masks] {source}: fusionando {len(partial_dirs)} backends...")
            ratios = _fuse_masks(images, partial_dirs, mask_dir)

        mean_ratio = round(sum(ratios) / len(ratios), 4) if ratios else 0.0
        heavily_masked = [img.name for img, r in zip(images, ratios) if r > 0.8]
        info["sources"][source] = {
            "images": len(images),
            "mean_masked_ratio": mean_ratio,
            "mean_masked_ratio_per_backend": per_backend,
            "heavily_masked": heavily_masked,
        }
        print(f"[masks] {source}: {len(images)} máscaras, {mean_ratio:.1%} de píxeles "
              f"enmascarados (por backend: {per_backend})")
        if heavily_masked:
            print(f"[masks] AVISO: {len(heavily_masked)} imágenes con >80% enmascarado "
                  "(quedará poca señal útil en ellas)")

    _link_openmvs_aliases(ctx, sources)

    ctx.metrics_dir.mkdir(parents=True, exist_ok=True)
    with open(ctx.metrics_dir / "masks_info.json", "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2, ensure_ascii=False)
