"""Backend 'wires': detector clásico de cables (líneas finas, oscuras, rectas y
largas) sin red neuronal. Complementa a SAM 3, que falla con cables de poco
contraste sobre fachadas.

Método (determinista, ~1 s/imagen con OpenCV):
  1. Trabaja a `work_width` px (los cables quedan de 1-3 px).
  2. Black top-hat (cierre - imagen): resalta estructuras oscuras más finas que
     `tophat_px`; se descarta lo que contrasta menos de `min_contrast`.
  3. Respuesta de línea: media de ese realce a lo largo de un segmento de
     `segment_px` en `orientations` direcciones (solo hasta `max_angle_deg`
     respecto a la horizontal: los cables rara vez son verticales; las rejas y
     postes sí). Un cable da respuesta alta en UNA dirección y baja en el resto
     (`anisotropy`); el follaje y las texturas responden en todas.
  4. Componentes conexas: se conservan las largas (`min_length` x ancho de
     trabajo) y delgadas (área / diagonal <= `max_thickness_px`).
  5. Reescalado a la foto original y dilatación `dilate_px`.

Limitaciones: pierde cables de contraste muy bajo y marca alguna línea fina
legítima (perfiles de rejas, bordes de cornisa, sardineles). Encadenado tras
segformer/sam3 solo añade zonas ignoradas.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

SUPPORTED_CLASSES: dict[str, int] = {}   # no usa clases: detector geométrico

DEFAULTS = {
    "work_width": 1600,
    "tophat_px": 7,
    "min_contrast": 12,
    "segment_px": 41,
    "orientations": 24,
    "max_angle_deg": 60.0,
    "min_line": 8.0,
    "anisotropy": 2.5,
    "min_length": 0.05,
    "max_thickness_px": 5.0,
    "bright": False,
}


def is_available() -> bool:
    try:
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def _has_cv2() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


# --- primitivas (OpenCV si está; si no, numpy + PIL, más lento) --------------

def black_tophat(gray, size: int):
    import numpy as np
    if _has_cv2():
        import cv2
        kernel = np.ones((size, size), np.uint8)
        return cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel).astype(np.int16)
    from PIL import Image, ImageFilter
    im = Image.fromarray(gray)
    closing = im.filter(ImageFilter.MaxFilter(size)).filter(ImageFilter.MinFilter(size))
    return np.asarray(closing).astype(np.int16) - gray.astype(np.int16)


def line_responses(resp, length: int, n_orient: int):
    """(n_orient, h, w): media de `resp` a lo largo de un segmento `length`
    centrado en cada píxel, por orientación (0 = horizontal)."""
    import numpy as np
    r = resp.astype(np.float32)
    half = length // 2
    out = np.empty((n_orient,) + r.shape, dtype=np.float32)
    if _has_cv2():
        import cv2
        size = 2 * half + 1
        for k in range(n_orient):
            th = math.pi * k / n_orient
            kernel = np.zeros((size, size), np.float32)
            dx, dy = math.cos(th) * half, math.sin(th) * half
            cv2.line(kernel, (int(round(half - dx)), int(round(half - dy))),
                     (int(round(half + dx)), int(round(half + dy))), 1.0, 1)
            kernel /= kernel.sum()
            out[k] = cv2.filter2D(r, -1, kernel, borderType=cv2.BORDER_REPLICATE)
        return out
    for k in range(n_orient):
        th = math.pi * k / n_orient
        dx, dy = math.cos(th), math.sin(th)
        acc = np.zeros_like(r)
        for t in range(-half, half + 1):
            acc += np.roll(r, (int(round(t * dy)), int(round(t * dx))), axis=(0, 1))
        out[k] = acc / (2 * half + 1)
    return out


def _label_pixels_numpy(mask):
    """Componentes 8-conexas por union-find vectorizado (respaldo sin OpenCV)."""
    import numpy as np
    h, w = mask.shape
    idx = np.arange(h * w).reshape(h, w)
    pairs = []
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        ys = slice(0, h - dy)
        ys2 = slice(dy, h)
        xs = slice(max(0, -dx), w - max(0, dx))
        xs2 = slice(max(0, dx), w - max(0, -dx))
        keep = mask[ys, xs] & mask[ys2, xs2]
        pairs.append(np.stack([idx[ys, xs][keep], idx[ys2, xs2][keep]], 1))
    edges = np.concatenate(pairs)
    labels = np.arange(h * w, dtype=np.int64)
    a, b = edges[:, 0], edges[:, 1]
    while True:
        m = np.minimum(labels[a], labels[b])
        before = labels.copy()
        np.minimum.at(labels, a, m)
        np.minimum.at(labels, b, m)
        while True:
            nl = labels[labels]
            if np.array_equal(nl, labels):
                break
            labels = nl
        if np.array_equal(labels, before):
            break
    labels = labels.reshape(h, w)
    labels[~mask] = -1
    return labels


def filter_components(mask, min_len: float, max_thick: float):
    """Conserva componentes con diagonal del bbox >= min_len y
    área / diagonal <= max_thick (largas y delgadas)."""
    import numpy as np
    if not mask.any():
        return mask
    if _has_cv2():
        import cv2
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
        wbox = stats[:, cv2.CC_STAT_WIDTH].astype(np.float32)
        hbox = stats[:, cv2.CC_STAT_HEIGHT].astype(np.float32)
        area = stats[:, cv2.CC_STAT_AREA].astype(np.float32)
        diag = np.hypot(wbox, hbox)
        keep = (diag >= min_len) & (area / np.maximum(diag, 1.0) <= max_thick)
        keep[0] = False                               # fondo
        return keep[labels]
    labels = _label_pixels_numpy(mask)
    ys, xs = np.nonzero(mask)
    uniq, inv = np.unique(labels[ys, xs], return_inverse=True)
    n = len(uniq)
    area = np.bincount(inv, minlength=n).astype(np.float32)
    x0 = np.full(n, np.inf); x1 = np.full(n, -np.inf); y0 = np.full(n, np.inf); y1 = np.full(n, -np.inf)
    np.minimum.at(x0, inv, xs); np.maximum.at(x1, inv, xs)
    np.minimum.at(y0, inv, ys); np.maximum.at(y1, inv, ys)
    diag = np.hypot(x1 - x0 + 1, y1 - y0 + 1)
    keep = (diag >= min_len) & (area / diag <= max_thick)
    out = np.zeros_like(mask)
    out[ys[keep[inv]], xs[keep[inv]]] = True
    return out


# --- detector -----------------------------------------------------------------

def detect_wires(img, cfg: dict):
    """PIL.Image -> máscara bool (H, W) de la foto original: True = cable."""
    import numpy as np
    from PIL import Image

    p = {**DEFAULTS, **{k: v for k, v in cfg.items() if k in DEFAULTS}}
    w0, h0 = img.size
    work_w = int(min(p["work_width"], w0))
    scale = work_w / w0
    gray_img = img.convert("L").resize((work_w, max(1, int(h0 * scale))), Image.BILINEAR)
    gray = np.asarray(gray_img)
    n_orient = int(p["orientations"])
    angles = np.array([180.0 * k / n_orient for k in range(n_orient)])
    allowed = np.minimum(angles, 180.0 - angles) <= float(p["max_angle_deg"])

    def pass_(g):
        th = np.clip(black_tophat(g, int(p["tophat_px"])), 0, None)
        th[th < int(p["min_contrast"])] = 0
        lr = line_responses(th, int(p["segment_px"]), n_orient)
        best = lr[allowed].max(axis=0)
        ratio = best / (lr.mean(axis=0) + 1e-3)
        return (best >= float(p["min_line"])) & (ratio >= float(p["anisotropy"]))

    mask = pass_(gray)
    if p["bright"]:                       # también líneas claras sobre fondo oscuro
        mask |= pass_(255 - gray)
    mask = filter_components(mask, min_len=float(p["min_length"]) * work_w,
                             max_thick=float(p["max_thickness_px"]))
    full = Image.fromarray(mask.astype(np.uint8) * 255).resize((w0, h0), Image.NEAREST)
    return np.asarray(full) > 0


def generate(images: list[Path], out_dir: Path, masking_cfg: dict, ctx) -> list[dict]:
    import numpy as np
    from PIL import Image
    from .segformer import _dilate

    bcfg = (masking_cfg.get("backends") or {}).get("wires") or {}
    dilate_px = int(bcfg.get("dilate_px", masking_cfg.get("dilate_px", 15)))
    source = images[0].parent.name if images else ""
    params = {k: bcfg.get(k, v) for k, v in DEFAULTS.items()}
    print(f"[masks] wires ({source}): {len(images)} imágenes, "
          f"{'OpenCV' if _has_cv2() else 'numpy/PIL (lento)'}, dilate={dilate_px}px, "
          f"contraste>={params['min_contrast']} línea>={params['min_line']} "
          f"anisotropía>={params['anisotropy']} ángulo<={params['max_angle_deg']}°")
    results = []
    t0 = time.time()
    for i, image in enumerate(images, 1):
        with Image.open(image) as img:
            wires = detect_wires(img, bcfg)
        wires = _dilate(wires, dilate_px)
        keep = np.where(wires, 0, 255).astype(np.uint8)
        Image.fromarray(keep).save(out_dir / (image.name + ".png"))
        ratio = float(wires.mean())
        results.append({"image": image.name, "masked_ratio": round(ratio, 4)})
        if i % 50 == 0 or i == len(images):
            print(f"[masks] wires ({source}): {i}/{len(images)} ({time.time() - t0:.0f}s)")
    return results
