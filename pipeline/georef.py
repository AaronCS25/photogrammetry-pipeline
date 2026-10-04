"""Etapa 'georef' (opcional): lleva el modelo sparse a un marco métrico y útil.

Sin esta etapa el modelo vive en el marco arbitrario de COLMAP (orientación,
escala y origen sin significado). Con ella:

  1. `colmap model_aligner` alinea el modelo con el GPS del EXIF (leído de la
     base de datos) a coordenadas ENU: metros reales, eje Z hacia arriba.
     Si no hay GPS suficiente y `fallback_plane` está activo, se endereza con
     el plano principal (orientación correcta, escala NO métrica).
  2. Se traslada el origen al centro de la escena (cómodo en Blender y base
     para ensamblar reconstrucciones por tiles).
  3. Opcionalmente se calcula una región de interés (ROI): una caja alineada a
     los ejes alrededor del centro. OpenMVS solo densifica y malla dentro de
     ella -> fuera quedan los edificios vecinos.

Salidas: colmap/sparse_geo/ (modelo final), colmap/roi.txt (formato OBB de
OpenMVS) y metrics/georef_info.json.
"""

from __future__ import annotations

import json
import shutil
import statistics
from pathlib import Path

from .config import Context
from .executor import CommandError, run_cmd
from .sfm import best_model_dir, database_path


def georef_enabled(ctx: Context) -> bool:
    return bool((ctx.cfg.get("georef") or {}).get("enabled"))


def georef_model_dir(ctx: Context) -> Path:
    return ctx.colmap_dir / "sparse_geo"


def roi_file(ctx: Context) -> Path:
    return ctx.colmap_dir / "roi.txt"


def active_model_dir(ctx: Context) -> Path:
    """Modelo sparse que deben usar las etapas posteriores a sfm: el
    georreferenciado si georef está activo, si no el elegido por sfm."""
    if georef_enabled(ctx):
        final = georef_model_dir(ctx)
        if not (final / "images.bin").is_file():
            raise CommandError("georef.enabled=true pero falta el modelo georreferenciado "
                               f"({final}): ejecutar la etapa 'georef'.")
        return final
    return best_model_dir(ctx)


def roi_active(ctx: Context) -> bool:
    """True si hay una ROI calculada para este experimento."""
    gcfg = ctx.cfg.get("georef") or {}
    return bool(gcfg.get("enabled") and (gcfg.get("roi") or {}).get("enabled")
                and roi_file(ctx).is_file())


# ---------------------------------------------------------------------------
# Geometría (sin dependencias: testeable sin COLMAP)
# ---------------------------------------------------------------------------

def _quat_to_rot(qw: float, qx: float, qy: float, qz: float) -> list[list[float]]:
    return [
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
    ]


def parse_camera_centers(images_txt: Path) -> dict[str, tuple[float, float, float]]:
    """Centros de cámara (C = -R^T t) por nombre de imagen, desde images.txt de COLMAP."""
    centers: dict[str, tuple[float, float, float]] = {}
    # Tras los comentarios, cada imagen ocupa DOS líneas: pose y puntos 2D. La
    # segunda puede estar vacía, así que no se descartan líneas en blanco.
    lines = [ln for ln in images_txt.read_text(encoding="utf-8", errors="replace").splitlines()
             if not ln.startswith("#")]
    for header in lines[0::2]:           # líneas pares: pose; impares: puntos 2D
        parts = header.split()
        if len(parts) < 10:
            continue
        qw, qx, qy, qz, tx, ty, tz = (float(v) for v in parts[1:8])
        name = " ".join(parts[9:])
        r = _quat_to_rot(qw, qx, qy, qz)
        centers[name] = (
            -(r[0][0] * tx + r[1][0] * ty + r[2][0] * tz),
            -(r[0][1] * tx + r[1][1] * ty + r[2][1] * tz),
            -(r[0][2] * tx + r[1][2] * ty + r[2][2] * tz),
        )
    return centers


def scene_center(centers: dict[str, tuple], source: str | None = None,
                 ground_below_m: float = 2.0) -> tuple[float, float, float]:
    """Centro de la escena: mediana XY de las cámaras (opcionalmente de una sola
    fuente, p. ej. la órbita del dron) y suelo estimado bajo la cámara más baja."""
    pts = [c for n, c in centers.items() if source is None or n.startswith(source + "/")]
    if not pts:
        raise CommandError(f"georef: no hay cámaras de la fuente '{source}' para centrar")
    cx = statistics.median(p[0] for p in pts)
    cy = statistics.median(p[1] for p in pts)
    z_ground = min(p[2] for p in centers.values()) - ground_below_m
    return cx, cy, z_ground


def compute_roi(centers: dict[str, tuple], center: tuple[float, float, float],
                roi_cfg: dict, source: str | None = None) -> dict:
    """Caja alineada a los ejes en el marco YA CENTRADO (origen = centro, z=0 ~ suelo)."""
    cx, cy, z_ground = center
    pts = [c for n, c in centers.items() if source is None or n.startswith(source + "/")]
    dists = sorted(((p[0] - cx) ** 2 + (p[1] - cy) ** 2) ** 0.5 for p in pts)
    radius = roi_cfg.get("radius_m")
    if radius is None:
        radius = statistics.median(dists) * float(roi_cfg.get("radius_factor", 1.0))
    radius = float(radius)
    z_min = -float(roi_cfg.get("below_m", 3.0))
    height = roi_cfg.get("height_m")
    z_max = float(height) if height is not None else max(p[2] for p in centers.values()) - z_ground
    return {
        "center": [0.0, 0.0, (z_min + z_max) / 2],
        "half_extent": [radius, radius, (z_max - z_min) / 2],
        "radius_m": round(radius, 2),
        "z_range_m": [round(z_min, 2), round(z_max, 2)],
    }


def write_roi_file(path: Path, roi: dict) -> None:
    """Formato de texto del OBB de OpenMVS (operator>> de TOBB): rotación 3x3,
    posición (centro) y semiextensiones. Caja alineada a ejes = rotación identidad."""
    c, e = roi["center"], roi["half_extent"]
    path.write_text(
        "1 0 0\n0 1 0\n0 0 1\n"
        f"{c[0]:.6f} {c[1]:.6f} {c[2]:.6f}\n"
        f"{e[0]:.6f} {e[1]:.6f} {e[2]:.6f}\n",
        encoding="utf-8",
    )


def write_translation(path: Path, t: tuple[float, float, float]) -> None:
    """Sim3d de COLMAP en texto: 'scale qw qx qy qz tx ty tz' (aquí solo traslación)."""
    path.write_text(f"1 1 0 0 0 {t[0]:.9f} {t[1]:.9f} {t[2]:.9f}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Etapa
# ---------------------------------------------------------------------------

def _model_to_txt(ctx: Context, model: Path, out: Path, log: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    run_cmd(["colmap", "model_converter", "--input_path", model,
             "--output_path", out, "--output_type", "TXT"], log, echo=False)
    return out / "images.txt"


def run_georef(ctx: Context) -> None:
    gcfg = ctx.cfg.get("georef") or {}
    if not gcfg.get("enabled"):
        print("[georef] georef.enabled = false: etapa omitida")
        return
    log = ctx.logs_dir / "georef.log"
    source_model = best_model_dir(ctx)
    aligned = ctx.colmap_dir / "sparse_aligned"
    final = georef_model_dir(ctx)
    for d in (aligned, final):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    roi_file(ctx).unlink(missing_ok=True)

    # 1) Alineación: ENU con GPS (métrico) o plano principal (no métrico)
    alignment = str(gcfg.get("alignment_type", "enu")).lower()
    metric = alignment == "enu"

    def align(kind: str) -> None:
        cmd = ["colmap", "model_aligner", "--input_path", source_model,
               "--output_path", aligned, "--alignment_type", kind]
        if kind == "enu":
            cmd += ["--database_path", database_path(ctx), "--ref_is_gps", "1",
                    "--min_common_images", str(gcfg.get("min_common_images", 3)),
                    "--alignment_max_error", str(gcfg.get("max_error_m", 3.0))]
        run_cmd(cmd, log)
        if not (aligned / "images.bin").is_file():
            raise CommandError(f"model_aligner ({kind}) no produjo un modelo en {aligned}")

    try:
        align(alignment)
    except CommandError as exc:
        if alignment == "enu" and gcfg.get("fallback_plane", True):
            print("[georef] AVISO: alineación GPS (enu) falló — ¿fotos sin GPS en el EXIF? "
                  "Se endereza con el plano principal: orientación correcta, escala NO métrica.")
            alignment, metric = "plane", False
            align("plane")
        else:
            raise CommandError(f"georef: {exc}")

    # 2) Centrado: trasladar el origen al centro de la escena
    centers = parse_camera_centers(_model_to_txt(ctx, aligned, ctx.colmap_dir / "_aligned_txt", log))
    source = gcfg.get("center_source")
    center = scene_center(centers, source, float(gcfg.get("ground_below_min_camera_m", 2.0)))
    if gcfg.get("center", True):
        tfile = ctx.colmap_dir / "georef_translation.txt"
        write_translation(tfile, (-center[0], -center[1], -center[2]))
        run_cmd(["colmap", "model_transformer", "--input_path", aligned,
                 "--output_path", final, "--transform_path", tfile], log)
    else:
        shutil.copytree(aligned, final, dirs_exist_ok=True)
        center = (0.0, 0.0, 0.0)
    if not (final / "images.bin").is_file():
        raise CommandError(f"georef: no se generó el modelo final en {final}")
    shutil.rmtree(ctx.colmap_dir / "_aligned_txt", ignore_errors=True)

    info: dict = {
        "alignment_type": alignment,
        "metric": metric,
        "cameras": len(centers),
        "center_source": source,
        # Traslación aplicada sobre el marco ENU de model_aligner (cuyo origen es
        # la primera imagen con GPS de la base de datos, altitud absoluta):
        "enu_offset_m": [round(v, 3) for v in center],
    }

    # 3) ROI (solo tiene sentido en un marco métrico y centrado)
    roi_cfg = gcfg.get("roi") or {}
    if roi_cfg.get("enabled"):
        if not metric or not gcfg.get("center", True):
            print("[georef] AVISO: ROI omitida (requiere alineación métrica 'enu' y center: true)")
        else:
            roi = compute_roi(centers, center, roi_cfg, source)
            write_roi_file(roi_file(ctx), roi)
            info["roi"] = roi
            print(f"[georef] ROI: radio {roi['radius_m']} m, z {roi['z_range_m']} m "
                  f"-> {roi_file(ctx).name}")

    ctx.metrics_dir.mkdir(parents=True, exist_ok=True)
    with open(ctx.metrics_dir / "georef_info.json", "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)
    print(f"[georef] modelo {'métrico (ENU, metros)' if metric else 'enderezado (no métrico)'}"
          f", {len(centers)} cámaras, origen en el centro de la escena")
