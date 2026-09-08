"""Backend de segmentación: SegFormer fine-tuned en Cityscapes (Hugging Face).

Cityscapes cubre exactamente las clases urbanas relevantes (person, car,
vegetation, pole...). Las dependencias pesadas (torch/transformers) se importan
de forma perezosa dentro de generate(), de modo que este módulo puede
importarse en contenedores sin ML para validar la configuración.
"""

from __future__ import annotations

from pathlib import Path

from ..executor import CommandError

# Ids de clase del dataset Cityscapes (trainIds estándar de 19 clases)
SUPPORTED_CLASSES = {
    "road": 0, "sidewalk": 1, "building": 2, "wall": 3, "fence": 4,
    "pole": 5, "traffic_light": 6, "traffic_sign": 7, "vegetation": 8,
    "terrain": 9, "sky": 10, "person": 11, "rider": 12, "car": 13,
    "truck": 14, "bus": 15, "train": 16, "motorcycle": 17, "bicycle": 18,
}

DEFAULT_MODEL = "nvidia/segformer-b5-finetuned-cityscapes-1024-1024"


def is_available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return True
    except ImportError:
        return False


def resolve_class_ids(classes: list[str]) -> list[int]:
    unknown = [c for c in classes if c not in SUPPORTED_CLASSES]
    if unknown:
        raise CommandError(
            f"Clases no soportadas por el backend segformer: {unknown}. "
            f"Soportadas: {sorted(SUPPORTED_CLASSES)}"
        )
    return [SUPPORTED_CLASSES[c] for c in classes]


def _dilate(masked, dilate_px: int):
    """Expande la zona enmascarada dilate_px píxeles (cv2 si está, si no PIL)."""
    if dilate_px <= 0:
        return masked
    import numpy as np
    try:
        import cv2
        kernel = np.ones((2 * dilate_px + 1, 2 * dilate_px + 1), np.uint8)
        return cv2.dilate(masked.astype(np.uint8), kernel, iterations=1).astype(bool)
    except ImportError:
        from PIL import Image, ImageFilter
        img = Image.fromarray((masked * 255).astype(np.uint8))
        img = img.filter(ImageFilter.MaxFilter(2 * dilate_px + 1))
        return np.array(img) > 0


def generate(images: list[Path], out_dir: Path, masking_cfg: dict) -> list[dict]:
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, SegformerForSemanticSegmentation

    backend_cfg = (masking_cfg.get("backends") or {}).get("segformer") or {}
    model_id = backend_cfg.get("model_id", DEFAULT_MODEL)
    class_ids = resolve_class_ids(masking_cfg.get("classes") or [])
    dilate_px = int(masking_cfg.get("dilate_px", 15))

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[masks] backend segformer: modelo={model_id} device={device} "
          f"clases={masking_cfg.get('classes')} dilate={dilate_px}px")
    processor = AutoImageProcessor.from_pretrained(model_id)
    model = SegformerForSemanticSegmentation.from_pretrained(model_id).to(device).eval()

    results = []
    for i, img_path in enumerate(images, 1):
        with Image.open(img_path) as img:
            img = img.convert("RGB")
            width, height = img.size
            inputs = processor(images=img, return_tensors="pt").to(device)
        with torch.no_grad():
            logits = model(**inputs).logits
        upsampled = torch.nn.functional.interpolate(
            logits, size=(height, width), mode="bilinear", align_corners=False)
        segmentation = upsampled.argmax(dim=1)[0].cpu().numpy()

        masked = np.isin(segmentation, class_ids)
        masked = _dilate(masked, dilate_px)
        # Convención COLMAP/OpenMVS: 0 = ignorar, 255 = usar
        keep = np.where(masked, 0, 255).astype(np.uint8)
        Image.fromarray(keep).save(out_dir / (img_path.name + ".png"))

        ratio = round(float(masked.mean()), 4)
        results.append({"image": img_path.name, "masked_ratio": ratio})
        if i % 50 == 0 or i == len(images):
            print(f"[masks] {out_dir.name}: {i}/{len(images)} máscaras")
    return results
