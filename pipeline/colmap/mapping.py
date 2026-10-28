"""Sparse reconstruction, explicit component quality and actual model selection."""
import re
import subprocess
from pathlib import Path

from ..config import ConfigError
from ..executor import extra_args_to_cli, run_cmd


def analyze(model: Path) -> dict:
    result = subprocess.run(["colmap", "model_analyzer", "--path", str(model)],
                            capture_output=True, text=True, check=True)
    text = result.stdout + result.stderr
    stats = {"path": str(model)}
    for name, pattern in {"registered_images": r"Registered images:\s*(\d+)",
                          "points": r"Points:\s*(\d+)",
                          "reprojection_error": r"Mean reprojection error:\s*([\d.eE+-]+)",
                          "track_length": r"Mean track length:\s*([\d.eE+-]+)"}.items():
        m = re.search(pattern, text)
        if m:
            stats[name] = float(m.group(1)) if name in ("reprojection_error", "track_length") else int(m.group(1))
    if not stats.get("registered_images") or not stats.get("points"):
        raise ConfigError(f"Modelo vacío o no analizable: {model}")
    return stats


def run_mapping(ctx, cfg: dict, parent: Path | None = None) -> dict:
    sparse = ctx.colmap_dir / "sparse"
    sparse.mkdir(parents=True, exist_ok=True)
    cmd = ["colmap", "mapper", "--database_path", ctx.colmap_dir / "database.db",
           "--image_path", ctx.frames_dir, "--output_path", sparse,
           "--Mapper.num_threads", str(cfg.get("threads", 8))]
    if parent:
        cmd += ["--input_path", parent]
    cmd += extra_args_to_cli(cfg.get("extra_args"))
    run_cmd(cmd, ctx.logs_dir / "mapping.log")
    models = [p for p in sparse.iterdir() if p.is_dir() and (p / "images.bin").is_file()]
    if not models:
        raise ConfigError("Mapper no produjo modelos")
    stats = [analyze(p) for p in sorted(models)]
    best = max(stats, key=lambda s: (s["registered_images"], s["points"]))
    model = Path(best["path"])
    (ctx.colmap_dir / "best_model.txt").write_text(model.name)
    run_cmd(["colmap", "model_converter", "--input_path", model,
             "--output_path", ctx.colmap_dir / "sparse.ply", "--output_type", "PLY"],
            ctx.logs_dir / "mapping.log")
    return {"components": stats, "best": best, "disconnected": len(stats) > 1}
