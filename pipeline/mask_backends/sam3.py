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

API verificada contra el código de sam3 @ 660a5e9e (commit fijado en
containers/segmentation.def): build_sam3_image_model(checkpoint_path=...),
Sam3Processor(model, confidence_threshold=...), set_text_prompt devuelve
"masks" bool (N, 1, H, W) ya a la resolución original y "scores" filtrados.
La inferencia exige autocast bfloat16 (el MLP del backbone castea a bf16 sin
condición, como en los notebooks oficiales) → GPU Ampere o superior
(A6000/A100); en T4 (Turing) bf16 no está soportado de forma nativa.
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
    import torch

    if not torch.cuda.is_available():
        raise CommandError("SAM 3 requiere GPU (Sam3Processor corre en 'cuda').")
    major, minor = torch.cuda.get_device_capability()
    if major < 8:
        print(f"[masks] AVISO: GPU {torch.cuda.get_device_name()} (compute {major}.{minor}) "
              "sin bfloat16 nativo; SAM 3 puede fallar o ir muy lento. Pedir A6000/A100: "
              "--gres=shard:rtxa6000:N o shard:a100:N")
    # Igual que los notebooks oficiales de sam3: TF32 + autocast bf16 durante
    # toda la inferencia (sin autocast, el MLP del ViT mezcla bf16 y float32).
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    with torch.autocast("cuda", dtype=torch.bfloat16):
        return _generate(images, out_dir, masking_cfg)


def _generate(images: list[Path], out_dir: Path, masking_cfg: dict) -> list[dict]:
    import numpy as np
    from PIL import Image
    from .segformer import _dilate

    try:
        from sam3.model_builder import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor
    except ImportError as exc:
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
        model = build_sam3_image_model(checkpoint_path=checkpoint) if checkpoint \
            else build_sam3_image_model()
    except Exception as exc:
        raise CommandError(
            f"No se pudo cargar SAM 3 ({exc}). ¿Se descargaron los pesos gated de "
            "facebook/sam3 en el nodo maestro? Ver containers/README.md"
        )
    # El procesador filtra internamente por confianza (default 0.5); pasarle
    # nuestro umbral para que score_threshold < 0.5 tenga efecto real.
    processor = Sam3Processor(model, confidence_threshold=threshold)

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
