"""Subcomando 'previews': vistas previas reducidas de las máscaras de un experimento.

Para cada imagen elegida (repartidas a lo largo de la secuencia de cada fuente)
dibuja en magenta los píxeles que la máscara IGNORA (0 = ignorar) sobre el frame,
reducido a `size` px de lado mayor. Escribe:

  outputs/<escena>/<exp>/previews/masks/<fuente>/<imagen>.jpg
  outputs/<escena>/<exp>/previews/masks/index.json   (fracción enmascarada de cada una)

Necesita PIL, que está en el contenedor de segmentación (no en photogrammetry.sif).
Es trabajo ligero de CPU (decodificación JPEG reducida), apto para el nodo maestro:

  apptainer exec containers/segmentation.sif \\
      python3 -m pipeline previews --scene <escena> --experiment <exp> [--max 12] [--size 640]
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import REPO_ROOT
from .executor import CommandError

# Magenta: casi no aparece en escenas urbanas (el rojo se confundía con fachadas pintadas de rojo).
OVERLAY = (255, 0, 200)


def spread(items: list, count: int) -> list:
    """`count` elementos repartidos uniformemente (incluye primero y último)."""
    if count <= 0 or len(items) <= count:
        return list(items)
    if count == 1:
        return [items[0]]
    step = (len(items) - 1) / (count - 1)
    return [items[round(i * step)] for i in range(count)]


def mask_pairs(exp_dir: Path) -> dict[str, list[tuple[Path, Path]]]:
    """Fuente -> [(frame, máscara)] con máscara COLMAP masks/<fuente>/<imagen>.png."""
    pairs: dict[str, list[tuple[Path, Path]]] = {}
    masks_dir, frames_dir = exp_dir / "masks", exp_dir / "frames"
    if not masks_dir.is_dir():
        return pairs
    for source in sorted(p for p in masks_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for mask in sorted(source.glob("*.png")):
            image = frames_dir / source.name / mask.name[:-4]
            if image.is_file():
                pairs.setdefault(source.name, []).append((image, mask))
    return pairs


def overlay(image_path: Path, mask_path: Path, size: int):
    """Frame reducido con lo ignorado en magenta translúcido; devuelve (imagen, fracción ignorada)."""
    from PIL import Image

    with Image.open(image_path) as im:
        im.draft("RGB", (size, size))  # JPEG: decodifica ya reducido (fotos de 36 MP)
        im = im.convert("RGB")
        im.thumbnail((size, size))
    with Image.open(mask_path) as m:
        mask = m.convert("L").resize(im.size, Image.NEAREST)
    hist = mask.histogram()
    ratio = hist[0] / max(1, sum(hist))
    ignored = mask.point(lambda v: 255 if v == 0 else 0)
    im.paste(Image.blend(im, Image.new("RGB", im.size, OVERLAY), 0.55), mask=ignored)
    return im, ratio


def run_previews(scene: str, experiment: str, max_images: int = 12, size: int = 640,
                 output_root: str | None = None) -> Path:
    root = Path(output_root) if output_root else REPO_ROOT / "outputs"
    if not root.is_absolute():
        root = REPO_ROOT / root
    exp_dir = root / scene / experiment
    if not exp_dir.is_dir():
        raise CommandError(f"No existe el experimento {exp_dir}")
    pairs = mask_pairs(exp_dir)
    if not pairs:
        raise CommandError(f"{exp_dir} no tiene máscaras (masks/<fuente>/<imagen>.png) con su frame.")

    # Reparto del cupo entre fuentes, proporcional a su tamaño (al menos 1 por fuente).
    total = sum(len(v) for v in pairs.values())
    quota = {s: max(1, round(max_images * len(v) / total)) for s, v in pairs.items()}

    out = exp_dir / "previews" / "masks"
    shutil.rmtree(out, ignore_errors=True)
    items = []
    for source, source_pairs in pairs.items():
        (out / source).mkdir(parents=True, exist_ok=True)
        for image, mask in spread(source_pairs, quota[source]):
            preview, ratio = overlay(image, mask, size)
            name = f"{source}/{image.stem}.jpg"
            preview.save(out / name, quality=82)
            items.append({"source": source, "image": image.name, "file": name, "masked_ratio": round(ratio, 4),
                          "index": source_pairs.index((image, mask)), "of": len(source_pairs)})
            print(f"[previews] {name}: {ratio:.1%} enmascarado")
    index = {"scene": scene, "experiment": experiment, "size": size, "total_masks": total,
             "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "items": items}
    (out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"[previews] {len(items)} vistas previas en {out}")
    return out
