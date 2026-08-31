"""Etapas COLMAP: sparse (features + matching + mapper), undistort y denso opcional."""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import Context
from .executor import CommandError, extra_args_to_cli, run_cmd


def database_path(ctx: Context) -> Path:
    return ctx.colmap_dir / "database.db"


def sparse_dir(ctx: Context) -> Path:
    return ctx.colmap_dir / "sparse"


def undistorted_dir(ctx: Context) -> Path:
    return ctx.colmap_dir / "undistorted"


def best_model_dir(ctx: Context) -> Path:
    """Modelo sparse elegido; se persiste en colmap/best_model.txt."""
    record = ctx.colmap_dir / "best_model.txt"
    if record.is_file():
        candidate = sparse_dir(ctx) / record.read_text(encoding="utf-8").strip()
        if candidate.is_dir():
            return candidate
    raise CommandError(
        "No hay modelo sparse seleccionado. ¿Se ejecutó la etapa 'sfm'? "
        f"(se esperaba {record})"
    )


def _select_best_model(ctx: Context) -> Path:
    """COLMAP puede producir varios modelos (sparse/0, sparse/1, ...);
    se elige el de mayor images.bin como proxy de imágenes registradas."""
    models = [d for d in sorted(sparse_dir(ctx).iterdir()) if (d / "images.bin").is_file()]
    if not models:
        raise CommandError(
            f"El mapper de COLMAP no produjo ningún modelo en {sparse_dir(ctx)}. "
            "Revisar logs/sfm.log (posibles causas: muy pocos matches, frames borrosos, "
            "overlap insuficiente entre frames)."
        )
    best = max(models, key=lambda d: (d / "images.bin").stat().st_size)
    if len(models) > 1:
        print(
            f"[sfm] ADVERTENCIA: el mapper produjo {len(models)} modelos desconectados; "
            f"se usa '{best.name}'. Considerar más overlap o matching vocab_tree."
        )
    (ctx.colmap_dir / "best_model.txt").write_text(best.name, encoding="utf-8")
    return best


def _gpu_flag(ctx: Context, section: dict) -> str:
    return "1" if (ctx.gpu and section.get("use_gpu", True)) else "0"


def run_sfm(ctx: Context) -> None:
    cfg = ctx.cfg["colmap"]
    log = ctx.logs_dir / "sfm.log"

    # Run limpio de la etapa: BD y sparse previos fuera
    if database_path(ctx).exists():
        database_path(ctx).unlink()
    if sparse_dir(ctx).exists():
        shutil.rmtree(sparse_dir(ctx))
    (ctx.colmap_dir / "best_model.txt").unlink(missing_ok=True)
    ctx.colmap_dir.mkdir(parents=True, exist_ok=True)
    sparse_dir(ctx).mkdir(parents=True, exist_ok=True)

    if not ctx.frames_dir.is_dir() or not any(ctx.frames_dir.iterdir()):
        raise CommandError(f"No hay frames en {ctx.frames_dir}: ejecutar antes la etapa 'frames'.")

    # ---------------------------------------------------------- features
    fe = cfg["feature_extractor"]
    cmd = [
        "colmap", "feature_extractor",
        "--database_path", database_path(ctx),
        "--image_path", ctx.frames_dir,
        "--ImageReader.camera_model", cfg.get("camera_model", "OPENCV"),
        "--SiftExtraction.use_gpu", _gpu_flag(ctx, fe),
        "--SiftExtraction.max_image_size", str(fe.get("max_image_size", 2400)),
        "--SiftExtraction.max_num_features", str(fe.get("max_num_features", 8192)),
    ]
    if cfg.get("single_camera_per_source", True):
        cmd += ["--ImageReader.single_camera_per_folder", "1"]
    cmd += extra_args_to_cli(fe.get("extra_args"))
    run_cmd(cmd, log)

    # ---------------------------------------------------------- matching
    mt = cfg["matcher"]
    for method in mt.get("methods", ["sequential"]):
        if method == "sequential":
            cmd = [
                "colmap", "sequential_matcher",
                "--database_path", database_path(ctx),
                "--SiftMatching.use_gpu", _gpu_flag(ctx, mt),
                "--SequentialMatching.overlap", str(mt.get("sequential_overlap", 10)),
            ]
            if mt.get("loop_detection"):
                if not mt.get("vocab_tree_path"):
                    raise CommandError("matcher.loop_detection requiere matcher.vocab_tree_path")
                cmd += [
                    "--SequentialMatching.loop_detection", "1",
                    "--SequentialMatching.vocab_tree_path", str(mt["vocab_tree_path"]),
                ]
        elif method == "exhaustive":
            cmd = [
                "colmap", "exhaustive_matcher",
                "--database_path", database_path(ctx),
                "--SiftMatching.use_gpu", _gpu_flag(ctx, mt),
            ]
        elif method == "vocab_tree":
            if not mt.get("vocab_tree_path"):
                raise CommandError("matcher method 'vocab_tree' requiere matcher.vocab_tree_path")
            cmd = [
                "colmap", "vocab_tree_matcher",
                "--database_path", database_path(ctx),
                "--SiftMatching.use_gpu", _gpu_flag(ctx, mt),
                "--VocabTreeMatching.vocab_tree_path", str(mt["vocab_tree_path"]),
            ]
        elif method == "spatial":
            cmd = [
                "colmap", "spatial_matcher",
                "--database_path", database_path(ctx),
                "--SiftMatching.use_gpu", _gpu_flag(ctx, mt),
            ]
        else:
            raise CommandError(f"Método de matching no soportado: {method}")
        cmd += extra_args_to_cli(mt.get("extra_args"))
        run_cmd(cmd, log)

    # ---------------------------------------------------------- mapper
    cmd = [
        "colmap", "mapper",
        "--database_path", database_path(ctx),
        "--image_path", ctx.frames_dir,
        "--output_path", sparse_dir(ctx),
    ]
    cmd += extra_args_to_cli(cfg.get("mapper", {}).get("extra_args"))
    run_cmd(cmd, log)

    _select_best_model(ctx)


def run_undistort(ctx: Context) -> None:
    cfg = ctx.cfg["colmap"].get("undistort", {})
    out = undistorted_dir(ctx)
    if out.exists():
        shutil.rmtree(out)
    cmd = [
        "colmap", "image_undistorter",
        "--image_path", ctx.frames_dir,
        "--input_path", best_model_dir(ctx),
        "--output_path", out,
        "--output_type", "COLMAP",
        "--max_image_size", str(cfg.get("max_image_size", -1)),
    ]
    run_cmd(cmd, ctx.logs_dir / "undistort.log")


# ---------------------------------------------------------------------------
# Backend denso de COLMAP (alternativa a OpenMVS, útil para comparaciones)
# ---------------------------------------------------------------------------

def colmap_fused_ply(ctx: Context) -> Path:
    return ctx.colmap_dir / "dense" / "fused.ply"


def run_colmap_dense(ctx: Context) -> None:
    if not ctx.gpu:
        raise CommandError("El backend denso de COLMAP (PatchMatch) requiere GPU (runtime.gpu: true).")
    dcfg = ctx.cfg["dense"]["colmap"]
    log = ctx.logs_dir / "dense.log"
    workspace = undistorted_dir(ctx)

    pm = dcfg.get("patch_match", {})
    cmd = [
        "colmap", "patch_match_stereo",
        "--workspace_path", workspace,
        "--workspace_format", "COLMAP",
        "--PatchMatchStereo.geom_consistency",
        "1" if pm.get("geom_consistency", True) else "0",
        "--PatchMatchStereo.max_image_size", str(pm.get("max_image_size", -1)),
        "--PatchMatchStereo.window_radius", str(pm.get("window_radius", 5)),
    ]
    cmd += extra_args_to_cli(pm.get("extra_args"))
    run_cmd(cmd, log)

    fused = colmap_fused_ply(ctx)
    fused.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "colmap", "stereo_fusion",
        "--workspace_path", workspace,
        "--workspace_format", "COLMAP",
        "--input_type", "geometric",
        "--output_path", fused,
    ]
    cmd += extra_args_to_cli(dcfg.get("fusion", {}).get("extra_args"))
    run_cmd(cmd, log)


def run_colmap_mesher(ctx: Context) -> None:
    dcfg = ctx.cfg["dense"]["colmap"]
    mesher = str(dcfg.get("mesher", "none")).lower()
    if mesher == "none":
        print("[mesh] dense.colmap.mesher = none: no se genera malla con COLMAP")
        return
    log = ctx.logs_dir / "mesh.log"
    if mesher == "poisson":
        cmd = [
            "colmap", "poisson_mesher",
            "--input_path", colmap_fused_ply(ctx),
            "--output_path", ctx.colmap_dir / "dense" / "meshed_poisson.ply",
        ]
    elif mesher == "delaunay":
        cmd = [
            "colmap", "delaunay_mesher",
            "--input_path", undistorted_dir(ctx),
            "--output_path", ctx.colmap_dir / "dense" / "meshed_delaunay.ply",
        ]
    else:
        raise CommandError(f"dense.colmap.mesher no soportado: {mesher}")
    cmd += extra_args_to_cli(dcfg.get("mesher_extra_args"))
    run_cmd(cmd, log)
