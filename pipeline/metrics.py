"""Etapa 'metrics': consolida estadísticas del experimento en metrics/metrics.json.

Incluye: frames por fuente, estadísticas del modelo sparse (colmap
model_analyzer), tamaño de nube densa y malla (headers PLY), tamaños de archivo
y tiempos de pared por etapa.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import REPO_ROOT, Context
from . import mvs as mvs_mod
from .sfm import best_model_dir, colmap_fused_ply, undistorted_dir


def parse_ply_counts(ply_path: Path) -> dict:
    """Lee el header (ASCII) de un PLY binario o de texto: nº de vértices y caras."""
    counts: dict[str, int] = {}
    try:
        with open(ply_path, "rb") as fh:
            header = b""
            while b"end_header" not in header and len(header) < 65536:
                chunk = fh.read(4096)
                if not chunk:
                    break
                header += chunk
        for element, count in re.findall(rb"element\s+(\w+)\s+(\d+)", header):
            counts[element.decode()] = int(count)
    except OSError:
        pass
    return counts


def run_model_analyzer(ctx: Context) -> dict:
    """Ejecuta colmap model_analyzer sobre el modelo sparse elegido y parsea su salida."""
    try:
        model = best_model_dir(ctx)
    except Exception:
        return {}
    out_file = ctx.metrics_dir / "colmap_model_analyzer.txt"
    try:
        result = subprocess.run(
            ["colmap", "model_analyzer", "--path", str(model)],
            capture_output=True, text=True, timeout=600,
        )
        text = (result.stdout or "") + (result.stderr or "")
        out_file.write_text(text, encoding="utf-8")
    except (subprocess.SubprocessError, OSError) as exc:
        return {"error": f"model_analyzer no disponible: {exc}"}

    stats: dict = {"model": model.name}
    patterns = {
        "cameras": r"Cameras:\s*(\d+)",
        "images": r"Images:\s*(\d+)",
        "registered_images": r"Registered images:\s*(\d+)",
        "points3d": r"Points:\s*(\d+)",
        "observations": r"Observations:\s*(\d+)",
        "mean_track_length": r"Mean track length:\s*([\d.]+)",
        "mean_observations_per_image": r"Mean observations per (?:registered )?image:\s*([\d.]+)",
        "mean_reprojection_error_px": r"Mean reprojection error:\s*([\d.]+)",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, text)
        if m:
            value = m.group(1)
            stats[key] = float(value) if "." in value else int(value)
    return stats


def _file_stat(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return {"path": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path),
            "size_mb": round(path.stat().st_size / 1e6, 2)}


def run_metrics(ctx: Context, timings: dict[str, float]) -> None:
    metrics: dict = {
        "scene": ctx.scene,
        "experiment": ctx.experiment,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "notes": (ctx.cfg.get("experiment") or {}).get("notes", ""),
        "dense_backend": ctx.cfg["dense"].get("backend", "openmvs"),
        "timings_seconds": timings,
        "timings_total_seconds": round(sum(t for t in timings.values() if t), 2),
    }

    # Desglose interno de la etapa sfm (features / matching / mapper), si existe
    sfm_timings_file = ctx.metrics_dir / "sfm_timings.json"
    if sfm_timings_file.is_file():
        try:
            metrics["sfm_breakdown_seconds"] = json.loads(
                sfm_timings_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    # Frames por fuente
    frames: dict[str, int] = {}
    if ctx.frames_dir.is_dir():
        for source_dir in sorted(p for p in ctx.frames_dir.iterdir() if p.is_dir()):
            frames[source_dir.name] = sum(1 for f in source_dir.iterdir() if f.is_file())
    metrics["frames_per_source"] = frames
    metrics["frames_total"] = sum(frames.values())

    # Sparse (COLMAP)
    metrics["sparse"] = run_model_analyzer(ctx)
    if metrics["frames_total"] and metrics["sparse"].get("registered_images") is not None:
        metrics["sparse"]["registration_ratio"] = round(
            metrics["sparse"]["registered_images"] / metrics["frames_total"], 4
        )

    # Denso
    dense: dict = {}
    openmvs_dense_ply = ctx.mvs_dir / "scene_dense.ply"
    if openmvs_dense_ply.is_file():
        dense["openmvs_points"] = parse_ply_counts(openmvs_dense_ply).get("vertex")
    fused = colmap_fused_ply(ctx)
    if fused.is_file():
        dense["colmap_fused_points"] = parse_ply_counts(fused).get("vertex")
    metrics["dense"] = dense

    # Mallas
    meshes: dict = {}
    for label, path in {
        "openmvs_mesh": ctx.mvs_dir / "scene_mesh.ply",
        "openmvs_mesh_refined": ctx.mvs_dir / "scene_mesh_refined.ply",
        "colmap_poisson": ctx.colmap_dir / "dense" / "meshed_poisson.ply",
        "colmap_delaunay": ctx.colmap_dir / "dense" / "meshed_delaunay.ply",
    }.items():
        counts = parse_ply_counts(path) if path.is_file() else {}
        if counts:
            meshes[label] = {"vertices": counts.get("vertex"), "faces": counts.get("face")}
    metrics["meshes"] = meshes

    # Artefactos principales y sus tamaños
    artifacts = {}
    for label, path in {
        "dense_ply": openmvs_dense_ply,
        "colmap_fused_ply": fused,
        "mesh_ply": ctx.mvs_dir / "scene_mesh.ply",
        "mesh_refined_ply": ctx.mvs_dir / "scene_mesh_refined.ply",
        "texture_obj": ctx.mvs_dir / "scene_texture.obj",
        "texture_ply": ctx.mvs_dir / "scene_texture.ply",
        "scene_mvs": ctx.mvs_dir / mvs_mod.SCENE,
        "scene_dense_mvs": ctx.mvs_dir / mvs_mod.SCENE_DENSE,
    }.items():
        stat = _file_stat(path)
        if stat:
            artifacts[label] = stat
    metrics["artifacts"] = artifacts

    with open(ctx.metrics_dir / "metrics.json", "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2, ensure_ascii=False)

    with open(ctx.metrics_dir / "timings.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["stage", "seconds"])
        for stage, seconds in timings.items():
            writer.writerow([stage, seconds])

    print(f"[metrics] escrito {ctx.metrics_dir / 'metrics.json'}")
