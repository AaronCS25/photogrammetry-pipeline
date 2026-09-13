"""Backend SAM 3 (Meta, "Segment Anything with Concepts"): segmentación por
prompts de texto libre. Pensado para conceptos que Cityscapes no tiene, en
especial estructuras finas: cables, líneas eléctricas, postes de alumbrado.

Requisitos: contenedor con el paquete `sam3` (repo facebookresearch/sam3,
Python >= 3.12, PyTorch >= 2.7, CUDA >= 12.6) y los pesos gated de Hugging
Face (facebook/sam3) descargados en la caché del nodo maestro.

Config (masking.backends.sam3):
  prompts: ["power line", "electric cable", "wire", "utility pole"]
  score_threshold: 0.5      # confianza mínima de cada instancia detectada
  checkpoint: null          # ruta local a los pesos; null = caché de HF

NOTA: la API del paquete sam3 se tomó del README oficial (build_sam3_image_model,
Sam3Processor.set_image / set_text_prompt). Los puntos marcados VERIFICAR se
confirman en el primer run real; los mensajes de error están pensados para
diagnosticarlos rápido.
"""

from __future__ import annotations

from pathlib import Path

from ..executor import CommandError

SUPPORTED_CLASSES: dict[str, int] = {}   # vocabulario abierto: usa prompts, no clases

DEFAULT_PROMPTS = ["power line", "electric cable", "wire", "utility pole"]


def is_available() -> bool:
    try:
        import torch  # noqa: F401
        import sam3  # noqa: F401
        return True
    except ImportError:
        return False


def _to_bool_mask(mask, height: int, width: int):
    """Normaliza una máscara de instancia (tensor/ndarray, con o sin batch) a
    bool (H, W), reescalando si el modelo la devuelve a otra resolución."""
    import numpy as np
    import torch

    if isinstance(mask, torch.Tensor):
        mask = mask.detach().cpu()
        while mask.dim() > 2:
            mask = mask[0]
        mask = mask.numpy()
    mask = np.asarray(mask)
    while mask.ndim > 2:
        mask = mask[0]
    if mask.dtype != bool:
        mask = mask > 0.5 if mask.dtype.kind == "f" else mask > 0
    if mask.shape != (height, width):
        from PIL import Image
        mask = np.array(
            Image.fromarray(mask.astype(np.uint8) * 255).resize((width, height), Image.NEAREST)
        ) > 0
    return mask


def generate(images: list[Path], out_dir: Path, masking_cfg: dict, ctx) -> list[dict]:
    import numpy as np
    from PIL import Image
    from .segformer import _dilate

    try:
        from sam3.model_builder import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor
    except ImportError as exc:  # VERIFICAR: rutas de import según versión del repo
        raise CommandError(f"No se pudo importar la API de sam3 ({exc}). "
                           "Revisar la versión instalada en containers/segmentation.def")

    bcfg = (masking_cfg.get("backends") or {}).get("sam3") or {}
    prompts = bcfg.get("prompts") or DEFAULT_PROMPTS
    if isinstance(prompts, str):          # `prompts: power line` (sin corchetes)
        prompts = [prompts]
    prompts = [str(p) for p in prompts]
    threshold = float(bcfg.get("score_threshold", 0.5))
    checkpoint = bcfg.get("checkpoint")
    dilate_px = int(bcfg.get("dilate_px", masking_cfg.get("dilate_px", 15)))

    print(f"[masks] backend sam3: prompts={prompts} umbral={threshold} dilate={dilate_px}px")
    try:
        # VERIFICAR: nombre del kwarg para pesos locales (checkpoint_path) si se usa
        model = build_sam3_image_model(checkpoint_path=checkpoint) if checkpoint \
            else build_sam3_image_model()
    except Exception as exc:
        raise CommandError(
            f"No se pudo cargar SAM 3 ({exc}). ¿Se descargaron los pesos gated de "
            "facebook/sam3 en el nodo maestro? Ver containers/README.md"
        )
    # El procesador filtra internamente por confianza (default 0.5); pasarle
    # nuestro umbral para que score_threshold < 0.5 tenga efecto real.
    try:
        processor = Sam3Processor(model, confidence_threshold=threshold)
    except TypeError:  # VERIFICAR: nombre del kwarg en la versión instalada
        print("[masks] AVISO: Sam3Processor no acepta confidence_threshold; "
              "umbrales < 0.5 no tendrán efecto (filtro interno del modelo)")
        processor = Sam3Processor(model)

    results = []
    for i, img_path in enumerate(images, 1):
        with Image.open(img_path) as img:
            img = img.convert("RGB")
            width, height = img.size
            state = processor.set_image(img)
            masked = np.zeros((height, width), dtype=bool)
            for prompt in prompts:
                output = processor.set_text_prompt(state=state, prompt=prompt)
                masks, scores = output["masks"], output["scores"]
                for mask, score in zip(masks, scores):
                    if float(score) >= threshold:
                        masked |= _to_bool_mask(mask, height, width)

        masked = _dilate(masked, dilate_px)
        keep = np.where(masked, 0, 255).astype(np.uint8)
        Image.fromarray(keep).save(out_dir / (img_path.name + ".png"))
        results.append({"image": img_path.name, "masked_ratio": round(float(masked.mean()), 4)})
        if i % 50 == 0 or i == len(images):
            print(f"[masks] sam3 {out_dir.parent.name}: {i}/{len(images)} máscaras")
    return results
