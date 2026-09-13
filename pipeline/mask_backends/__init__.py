"""Registro de backends de segmentación semántica (piezas intercambiables).

Cada backend es un módulo con este contrato (sin importar torch a nivel de
módulo, para que validate/skip funcionen en contenedores sin ML):

  SUPPORTED_CLASSES: dict[str, int]   # nombre canónico -> id del modelo ({} si usa prompts)
  is_available() -> bool              # ¿están las dependencias (torch...)?
  generate(images, out_dir, masking_cfg, ctx) -> list[dict]
      # escribe <imagen>.png (255=usar, 0=ignorar) por cada imagen y
      # devuelve [{"image": ..., "masked_ratio": ...}, ...]

Varios backends pueden encadenarse (`masking.backend: [a, b]`); la etapa masks
fusiona sus salidas. Para añadir un backend nuevo: crear
pipeline/mask_backends/<nombre>.py con ese contrato y registrarlo aquí.
"""

from __future__ import annotations

import importlib

from ..executor import CommandError

KNOWN_BACKENDS = {
    "segformer": ".segformer",   # SegFormer-B5 Cityscapes: clases urbanas estándar
    "sam3": ".sam3",             # SAM 3: prompts de texto libre (cables, postes...)
    "manual": ".manual",         # PNGs pintados a mano en datasets/raw/<escena>/mask_overrides/
}


def get_backend(name: str):
    if name not in KNOWN_BACKENDS:
        raise CommandError(
            f"masking.backend desconocido: '{name}'. Disponibles: {sorted(KNOWN_BACKENDS)}"
        )
    return importlib.import_module(KNOWN_BACKENDS[name], package=__name__)
