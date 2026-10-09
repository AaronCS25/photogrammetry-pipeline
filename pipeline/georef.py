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
# Referencias GPS
# ---------------------------------------------------------------------------

def read_gps_priors(db_path: Path) -> tuple[list[tuple[str, float, float, float]], int]:
    """(nombre, lat, lon, alt) por imagen desde la base de datos de COLMAP
    (tabla pose_priors: posición = 3 doubles), descartando GPS inválido.

    Importa filtrar aquí en vez de pasarle la BD a model_aligner: COLMAP toma la
    PRIMERA referencia como origen del marco ENU, y los drones DJI escriben
    (0.0, 0.0) en las fotos tomadas antes de fijar satélites — con una de esas
    como origen, el "arriba" del modelo queda el del Golfo de Guinea.
    Devuelve (válidas, nº de inválidas).
    """
    import math
    import sqlite3
    import struct

    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT i.name, p.position FROM pose_priors p "
            "JOIN images i ON i.image_id = p.image_id ORDER BY i.image_id"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        con.close()

    valid: list[tuple[str, float, float, float]] = []
    invalid = 0
    for name, blob in rows:
        if not blob or len(blob) < 24:
            invalid += 1
            continue
        lat, lon, alt = struct.unpack("<3d", blob[:24])
        if (not all(math.isfinite(v) for v in (lat, lon, alt))
                or (abs(lat) < 1e-6 and abs(lon) < 1e-6)
                or abs(lat) > 90 or abs(lon) > 180):
            invalid += 1
            continue
        valid.append((name, lat, lon, alt))
    return valid, invalid


def reference_file_path(ctx: Context) -> Path | None:
    """georef.reference_file resuelto (relativo a la carpeta de la escena o absoluto)."""
    ref = (ctx.cfg.get("georef") or {}).get("reference_file")
    if not ref:
        return None
    path = Path(str(ref))
    return path if path.is_absolute() else ctx.raw_dir / path


def db_image_names(db_path: Path) -> list[str]:
    import sqlite3

    con = sqlite3.connect(str(db_path))
    try:
        return [r[0] for r in con.execute("SELECT name FROM images ORDER BY image_id")]
    finally:
        con.close()


_REF_COLUMNS = {
    "name": ("file", "name", "image", "filename"),
    "lat": ("lat", "latitude"),
    "lon": ("lon", "lng", "long", "longitude"),
    "alt": ("alt", "altitude", "elevation"),
}


def read_reference_file(path: Path, image_names: list[str]
                        ) -> tuple[list[tuple[str, float, float, float]], int]:
    """(nombre, lat, lon, alt) desde un CSV con cabecera, para imágenes sin GPS
    en el EXIF (p. ej. recortes de Street View, cuyo índice trae lat/lon).

    Columnas: file|name|image, lat, lon y opcionalmente alt (default 0: las
    cámaras quedan en un plano horizontal). Cada fila se empareja con una imagen
    de la base de datos por ruta ('fuente/archivo') o, si no, por nombre de
    archivo cuando es único. Devuelve (válidas, nº de filas descartadas).
    """
    import csv
    import math

    exact = {n.lower(): n for n in image_names}
    by_base: dict[str, list[str]] = {}
    for n in image_names:
        by_base.setdefault(n.rsplit("/", 1)[-1].lower(), []).append(n)

    with open(path, newline="", encoding="utf-8-sig") as fh:   # -sig: tolera BOM
        reader = csv.DictReader(fh)
        fields = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}
        col = {key: next((fields[a] for a in aliases if a in fields), None)
               for key, aliases in _REF_COLUMNS.items()}
        missing = [k for k in ("name", "lat", "lon") if col[k] is None]
        if missing:
            raise CommandError(
                f"georef.reference_file {path}: faltan columnas {missing} "
                f"(cabecera encontrada: {reader.fieldnames})")
        valid: list[tuple[str, float, float, float]] = []
        seen: set[str] = set()
        skipped = 0
        for row in reader:
            key = (row.get(col["name"]) or "").strip().replace("\\", "/").lower()
            same_base = by_base.get(key.rsplit("/", 1)[-1], [])
            name = exact.get(key) or (same_base[0] if len(same_base) == 1 else None)
            try:
                lat, lon = float(row[col["lat"]]), float(row[col["lon"]])
                alt = float(row.get(col["alt"]) or 0.0) if col["alt"] else 0.0
            except (TypeError, ValueError):
                skipped += 1
                continue
            if (name is None or name in seen
                    or not all(math.isfinite(v) for v in (lat, lon, alt))
                    or (abs(lat) < 1e-6 and abs(lon) < 1e-6)
                    or abs(lat) > 90 or abs(lon) > 180):
                skipped += 1
                continue
            seen.add(name)
            valid.append((name, lat, lon, alt))
    return valid, skipped


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
    def pct(p: float) -> float:
        return round(dists[min(len(dists) - 1, int(p * len(dists)))], 1) if dists else 0.0

    return {
        "center": [0.0, 0.0, (z_min + z_max) / 2],
        "half_extent": [radius, radius, (z_max - z_min) / 2],
        "radius_m": round(radius, 2),
        "z_range_m": [round(z_min, 2), round(z_max, 2)],
        # Para elegir radius_m a mano: distancia horizontal cámara->centro.
        # OJO: en vuelos oblicuos SOBRE el objetivo la mediana subestima su tamaño.
        "camera_distance_m": {"median": pct(0.5), "p90": pct(0.9), "max": pct(1.0)},
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

    gps_info: dict = {}
    # Posiciones desde archivo (en vez del EXIF). Si se pidió y no existe es un
    # error de configuración: no se degrada en silencio al plano principal.
    ref_path = reference_file_path(ctx)
    if ref_path is not None and not ref_path.is_file():
        raise CommandError(f"georef.reference_file no existe: {ref_path}")

    def align(kind: str) -> None:
        cmd = ["colmap", "model_aligner", "--input_path", source_model,
               "--output_path", aligned, "--alignment_type", kind,
               # COLMAP 3.11 lo exige (> 0) para CUALQUIER tipo, también 'plane'
               "--alignment_max_error", str(gcfg.get("max_error_m", 3.0))]
        if kind == "enu":
            min_common = int(gcfg.get("min_common_images", 3))
            if ref_path is not None:
                refs, invalid = read_reference_file(ref_path, db_image_names(database_path(ctx)))
                print(f"[georef] posiciones de {ref_path.name}: {len(refs)} imágenes "
                      f"emparejadas, {invalid} filas descartadas")
            else:
                refs, invalid = read_gps_priors(database_path(ctx))
                print(f"[georef] GPS en la base de datos: {len(refs)} válidos, {invalid} "
                      "descartados (sin fijar satélites: lat=lon=0)")
            if len(refs) < min_common:
                raise CommandError(f"solo {len(refs)} imágenes con GPS válido "
                                   f"(se necesitan {min_common})")
            # Lista propia de referencias: la primera (válida) será el origen ENU
            refs_file = ctx.colmap_dir / "georef_gps_refs.txt"
            refs_file.write_text(
                "".join(f"{n} {lat:.10f} {lon:.10f} {alt:.4f}\n" for n, lat, lon, alt in refs),
                encoding="utf-8")
            gps_info.update({
                "gps_source": ref_path.name if ref_path is not None else "exif",
                "gps_valid": len(refs), "gps_discarded": invalid,
                "enu_origin_gps": {"image": refs[0][0], "lat": refs[0][1],
                                   "lon": refs[0][2], "alt": refs[0][3]},
            })
            cmd += ["--ref_images_path", refs_file, "--ref_is_gps", "1",
                    "--min_common_images", str(min_common)]
        run_cmd(cmd, log)
        if not (aligned / "images.bin").is_file():
            raise CommandError(f"model_aligner ({kind}) no produjo un modelo en {aligned}")

    try:
        align(alignment)
    except CommandError as exc:
        if alignment == "enu" and gcfg.get("fallback_plane", True):
            print(f"[georef] AVISO: alineación GPS (enu) falló ({str(exc).splitlines()[0]}) — "
                  "¿fotos sin GPS en el EXIF? Para posiciones en un CSV: georef.reference_file. "
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
        # Traslación aplicada sobre el marco ENU de model_aligner, cuyo origen es
        # enu_origin_gps (primera imagen con GPS válido). Con ambos datos una
        # reconstrucción se puede ubicar junto a otras (tiles).
        "enu_offset_m": [round(v, 3) for v in center],
        **(gps_info if metric else {}),
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
                  f"-> {roi_file(ctx).name} | distancia cámara->centro: "
                  f"{roi['camera_distance_m']} m")
            if roi_cfg.get("radius_m") is None:
                print("[georef] AVISO: radio automático. Si las fotos se tomaron SOBRE el "
                      "objetivo (no en órbita alrededor), fijar roi.radius_m a mano.")

    ctx.metrics_dir.mkdir(parents=True, exist_ok=True)
    with open(ctx.metrics_dir / "georef_info.json", "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)
    print(f"[georef] modelo {'métrico (ENU, metros)' if metric else 'enderezado (no métrico)'}"
          f", {len(centers)} cámaras, origen en el centro de la escena")
