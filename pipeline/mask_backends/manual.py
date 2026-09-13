"""Backend 'manual': máscaras pintadas a mano que se fusionan con las automáticas.

Uso: colocar PNGs en datasets/raw/<escena>/mask_overrides/ (configurable con
masking.backends.manual.dir), con el mismo nombre que la máscara COLMAP de la
imagen (<imagen>.png, p. ej. IMG_0001.jpg.png) y opcionalmente dentro de la
subcarpeta de la fuente. Negro (0) = ignorar; los píxeles TRANSPARENTES se
tratan como "usar" (así se puede pintar solo trazos sobre un lienzo
transparente). Las imágenes sin override reciben una máscara toda blanca (sin
efecto). Al encadenarlo (`backend: [segformer, manual]`) las ediciones
sobreviven a regeneraciones.
"""

from __future__ import annotations

from pathlib import Path

SUPPORTED_CLASSES: dict[str, int] = {}   # no usa clases: lee archivos
DEFAULT_OVERRIDE_DIR = "mask_overrides"


def is_available() -> bool:
    return True   # solo necesita PIL, presente en todos los contenedores


def override_dir(masking_cfg: dict, ctx) -> Path:
    """Carpeta de overrides resuelta (relativa a la escena o absoluta);
    `dir: null` vuelve al default."""
    bcfg = (masking_cfg.get("backends") or {}).get("manual") or {}
    path = Path(bcfg.get("dir") or DEFAULT_OVERRIDE_DIR)
    return path if path.is_absolute() else ctx.raw_dir / path


def find_override(image_name: str, source: str, base: Path) -> Path | None:
    """Override de una imagen: <base>/<fuente>/<imagen>.png o <base>/<imagen>.png."""
    mask_name = image_name + ".png"
    for candidate in (base / source / mask_name, base / mask_name):
        if candidate.is_file():
            return candidate
    return None


def load_override(path: Path, width: int, height: int):
    """PNG pintado a mano -> uint8 (H, W) con 0 = ignorar, 255 = usar.
    Transparente cuenta como 'usar'; de lo contrario, oscuro (<128) = ignorar."""
    import numpy as np
    from PIL import Image

    with Image.open(path) as m:
        if m.size != (width, height):
            m = m.resize((width, height), Image.NEAREST)
        has_alpha = "A" in m.getbands()
        alpha = np.array(m.getchannel("A")) if has_alpha else None
        gray = np.array(m.convert("L"))
    keep = np.where(gray < 128, 0, 255).astype(np.uint8)
    if alpha is not None:
        keep[alpha == 0] = 255
    return keep


def generate(images: list[Path], out_dir: Path, masking_cfg: dict, ctx) -> list[dict]:
    import numpy as np
    from PIL import Image

    base = override_dir(masking_cfg, ctx)
    source = images[0].parent.name if images else ""
    results = []
    found = 0
    for image in images:
        with Image.open(image) as img:
            width, height = img.size
        override = find_override(image.name, source, base)
        if override is not None:
            keep = load_override(override, width, height)
            found += 1
        else:
            keep = np.full((height, width), 255, dtype=np.uint8)
        Image.fromarray(keep).save(out_dir / (image.name + ".png"))
        results.append({"image": image.name, "masked_ratio": round(float((keep == 0).mean()), 4)})
    print(f"[masks] manual ({source}): {found} overrides encontrados en {base}")
    return results
