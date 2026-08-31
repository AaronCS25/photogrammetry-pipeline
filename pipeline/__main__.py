"""CLI del pipeline.

  python3 -m pipeline run      --config configs/experiments/x.yaml --scene mi_escena
  python3 -m pipeline validate --config configs/experiments/x.yaml --scene mi_escena
  python3 -m pipeline report
"""

from __future__ import annotations

import argparse
import shutil
import sys

from .config import ConfigError, Context, load_config, make_context
from .executor import CommandError, StageRunner
from . import frames as frames_mod
from . import telemetry as telemetry_mod
from . import sfm as sfm_mod
from . import mvs as mvs_mod
from .metrics import run_metrics
from .report import run_report

# Orden canónico de etapas
STAGE_ORDER = ["frames", "telemetry", "sfm", "undistort", "dense", "mesh", "texture", "metrics"]


def _stage_functions(ctx: Context) -> dict:
    backend = str(ctx.cfg["dense"].get("backend", "openmvs")).lower()
    if backend == "openmvs":
        dense_fn = lambda: mvs_mod.run_densify(ctx)
        mesh_fn = lambda: mvs_mod.run_mesh(ctx)
        texture_fn = lambda: mvs_mod.run_texture(ctx)
    elif backend == "colmap":
        dense_fn = lambda: sfm_mod.run_colmap_dense(ctx)
        mesh_fn = lambda: sfm_mod.run_colmap_mesher(ctx)
        texture_fn = lambda: print("[texture] backend colmap: sin etapa de texturizado")
    else:
        raise ConfigError(f"dense.backend no soportado: {backend} (usar openmvs o colmap)")

    return {
        "frames": lambda: frames_mod.run_frames(ctx),
        "telemetry": lambda: telemetry_mod.run_telemetry(ctx),
        "sfm": lambda: sfm_mod.run_sfm(ctx),
        "undistort": lambda: sfm_mod.run_undistort(ctx),
        "dense": dense_fn,
        "mesh": mesh_fn,
        "texture": texture_fn,
    }


def _selected_stages(args: argparse.Namespace) -> list[str]:
    stages = list(STAGE_ORDER)
    if args.stages:
        requested = [s.strip() for s in args.stages.split(",") if s.strip()]
        unknown = [s for s in requested if s not in STAGE_ORDER]
        if unknown:
            raise ConfigError(f"Etapas desconocidas: {unknown}. Válidas: {STAGE_ORDER}")
        stages = [s for s in STAGE_ORDER if s in requested]
    if args.from_stage:
        if args.from_stage not in STAGE_ORDER:
            raise ConfigError(f"--from-stage desconocida: {args.from_stage}. Válidas: {STAGE_ORDER}")
        idx = STAGE_ORDER.index(args.from_stage)
        stages = [s for s in stages if STAGE_ORDER.index(s) >= idx]
    return stages


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    ctx = make_context(cfg, args.scene, force=args.force)
    ctx.prepare()
    ctx.dump_resolved_config()

    stages = _selected_stages(args)
    print(f"[pipeline] escena='{ctx.scene}' experimento='{ctx.experiment}'")
    print(f"[pipeline] etapas a ejecutar: {stages}")
    print(f"[pipeline] salida: {ctx.out_dir}")

    if not ctx.cfg.get("telemetry", {}).get("enabled", True) and "telemetry" in stages:
        stages.remove("telemetry")

    runner = StageRunner(ctx.markers_dir, force=args.force)
    functions = _stage_functions(ctx)
    try:
        for stage in stages:
            if stage == "metrics":
                continue  # siempre al final, fuera del sistema de marcadores
            runner.run(stage, functions[stage])
        if "metrics" in stages and ctx.cfg.get("metrics", {}).get("enabled", True):
            run_metrics(ctx, runner.collected_timings())
    except (CommandError, ConfigError) as exc:
        print(f"\n[pipeline] ERROR: {exc}", file=sys.stderr)
        print("[pipeline] las etapas completadas quedan marcadas; al relanzar se reanuda desde el fallo.",
              file=sys.stderr)
        return 1

    print("\n[pipeline] ✔ ejecución finalizada")
    if runner.executed:
        print(f"[pipeline] etapas ejecutadas: {runner.executed}")
    if runner.skipped:
        print(f"[pipeline] etapas reutilizadas de runs previos: {runner.skipped}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    ctx = make_context(cfg, args.scene)

    print(f"Escena:      {ctx.scene}  ({ctx.raw_dir})")
    print(f"Experimento: {ctx.experiment}")
    print(f"Salida:      {ctx.out_dir}")
    print(f"Backend denso: {cfg['dense'].get('backend')}")

    sources = frames_mod.discover_sources(ctx.raw_dir)
    if not sources:
        print("ERROR: no se encontraron videos en la escena.", file=sys.stderr)
        return 1
    print("\nFuentes detectadas:")
    for source, videos in sources.items():
        settings = frames_mod.source_settings(cfg, source)
        print(f"  {source}: {len(videos)} video(s) | fps={settings.get('fps')} "
              f"resize={settings.get('resize')} format={settings.get('format')}")
        for video in videos:
            srt = " (+.srt)" if any(video.with_suffix(e).is_file() for e in (".srt", ".SRT")) else ""
            print(f"    - {video.name}{srt}")

    if len(sources) > 1:
        methods = cfg["colmap"]["matcher"].get("methods", [])
        if methods == ["sequential"]:
            print("\nADVERTENCIA: escena multi-fuente con matching solo 'sequential'; "
                  "las fuentes no se conectarán entre sí. Añadir 'vocab_tree' o 'exhaustive'.")

    mt = cfg["colmap"]["matcher"]
    needs_vocab = "vocab_tree" in mt.get("methods", []) or mt.get("loop_detection")
    if needs_vocab:
        from .config import REPO_ROOT
        vt = mt.get("vocab_tree_path")
        if not vt:
            print("\nERROR: se requiere matcher.vocab_tree_path y no está definido.", file=sys.stderr)
            return 1
        vt_path = REPO_ROOT / vt if not str(vt).startswith("/") else vt
        if not __import__("pathlib").Path(vt_path).is_file():
            print(f"\nERROR: no existe el vocab tree: {vt_path}\n"
                  "Descargarlo en el nodo maestro: "
                  "wget https://demuc.de/colmap/vocab_tree_flickr100K_words256K.bin", file=sys.stderr)
            return 1

    print("\nBinarios en PATH:")
    for binary in ("ffmpeg", "ffprobe", "colmap", "DensifyPointCloud"):
        status = shutil.which(binary) or "NO ENCONTRADO (ok si se ejecutará dentro del contenedor)"
        print(f"  {binary}: {status}")

    print("\n✔ Configuración válida.")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    run_report(args.output_root, args.output)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Ejecuta el pipeline completo o etapas seleccionadas")
    p_run.add_argument("--config", help="YAML de experimento (se combina con configs/default.yaml)")
    p_run.add_argument("--scene", help="Nombre de la escena bajo datasets/raw/")
    p_run.add_argument("--stages", help="Lista separada por comas, ej: frames,sfm,dense")
    p_run.add_argument("--from-stage", help="Ejecutar desde esta etapa en adelante")
    p_run.add_argument("--force", action="store_true",
                       help="Re-ejecuta las etapas seleccionadas aunque estén marcadas como completadas")
    p_run.set_defaults(func=cmd_run)

    p_val = sub.add_parser("validate", help="Valida configuración y datos sin ejecutar nada")
    p_val.add_argument("--config")
    p_val.add_argument("--scene")
    p_val.set_defaults(func=cmd_validate)

    p_rep = sub.add_parser("report", help="Consolida los metrics.json en un CSV comparativo")
    p_rep.add_argument("--output-root", help="Raíz de outputs (por defecto: outputs/)")
    p_rep.add_argument("--output", help="Ruta del CSV de salida")
    p_rep.set_defaults(func=cmd_report)

    args = parser.parse_args()
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"ERROR de configuración: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
