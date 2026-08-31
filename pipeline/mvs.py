"""Etapas OpenMVS: InterfaceCOLMAP -> Densify -> ReconstructMesh -> RefineMesh -> TextureMesh.

Los archivos .mvs se encadenan (cada herramienta escribe la escena con su
resultado embebido), por lo que las etapas posteriores usan la salida .mvs de la
anterior. Todos los artefactos quedan en outputs/<escena>/<exp>/mvs/.
"""

from __future__ import annotations

from pathlib import Path

from .config import Context
from .executor import CommandError, extra_args_to_cli, run_cmd
from .sfm import undistorted_dir

SCENE = "scene.mvs"
SCENE_DENSE = "scene_dense.mvs"
SCENE_MESH = "scene_mesh.mvs"
SCENE_MESH_REFINED = "scene_mesh_refined.mvs"
SCENE_TEXTURE = "scene_texture.mvs"


def _cuda_args(ctx: Context) -> list[str]:
    # OpenMVS usa CUDA automáticamente; --cuda-device -2 la desactiva.
    return [] if ctx.gpu else ["--cuda-device", "-2"]


def _run(ctx: Context, cmd: list, log_name: str) -> None:
    run_cmd(cmd, ctx.logs_dir / log_name, cwd=ctx.mvs_dir)


def _require(ctx: Context, filename: str, produced_by: str) -> Path:
    path = ctx.mvs_dir / filename
    if not path.is_file():
        raise CommandError(f"Falta {path}; ¿se ejecutó la etapa '{produced_by}'?")
    return path


def run_densify(ctx: Context) -> None:
    ctx.mvs_dir.mkdir(parents=True, exist_ok=True)
    workspace = undistorted_dir(ctx)
    if not workspace.is_dir():
        raise CommandError(f"No existe {workspace}: ejecutar antes la etapa 'undistort'.")

    # 1) Conversión del workspace COLMAP a formato OpenMVS (.mvs)
    _run(ctx, [
        "InterfaceCOLMAP",
        "-w", ctx.mvs_dir,
        "-i", workspace,
        "-o", SCENE,
    ], "dense.log")

    # 2) Nube de puntos densa
    dcfg = ctx.cfg["openmvs"]["densify"]
    cmd = [
        "DensifyPointCloud",
        "-w", ctx.mvs_dir,
        SCENE,
        "-o", SCENE_DENSE,
        "--resolution-level", str(dcfg.get("resolution_level", 1)),
        "--number-views", str(dcfg.get("number_views", 0)),
    ]
    cmd += _cuda_args(ctx)
    cmd += extra_args_to_cli(dcfg.get("extra_args"))
    _run(ctx, cmd, "dense.log")
    _require(ctx, SCENE_DENSE, "dense")


def run_mesh(ctx: Context) -> None:
    mcfg = ctx.cfg["openmvs"]["mesh"]
    if not mcfg.get("enabled", True):
        print("[mesh] openmvs.mesh.enabled = false: etapa omitida")
        return
    _require(ctx, SCENE_DENSE, "dense")

    cmd = [
        "ReconstructMesh",
        "-w", ctx.mvs_dir,
        SCENE_DENSE,
        "-o", SCENE_MESH,
        "--decimate", str(mcfg.get("decimate", 1.0)),
    ]
    cmd += extra_args_to_cli(mcfg.get("extra_args"))
    _run(ctx, cmd, "mesh.log")

    rcfg = ctx.cfg["openmvs"]["refine"]
    if rcfg.get("enabled", True):
        cmd = [
            "RefineMesh",
            "-w", ctx.mvs_dir,
            SCENE_MESH,
            "-o", SCENE_MESH_REFINED,
            "--scales", str(rcfg.get("scales", 2)),
        ]
        cmd += _cuda_args(ctx)
        cmd += extra_args_to_cli(rcfg.get("extra_args"))
        _run(ctx, cmd, "mesh.log")


def run_texture(ctx: Context) -> None:
    tcfg = ctx.cfg["openmvs"]["texture"]
    if not tcfg.get("enabled", True):
        print("[texture] openmvs.texture.enabled = false: etapa omitida")
        return
    # Usar la malla refinada si existe; si no, la malla base
    if (ctx.mvs_dir / SCENE_MESH_REFINED).is_file():
        mesh_scene = SCENE_MESH_REFINED
    else:
        mesh_scene = _require(ctx, SCENE_MESH, "mesh").name

    cmd = [
        "TextureMesh",
        "-w", ctx.mvs_dir,
        mesh_scene,
        "-o", SCENE_TEXTURE,
        "--export-type", str(tcfg.get("export_type", "obj")),
    ]
    cmd += _cuda_args(ctx)
    cmd += extra_args_to_cli(tcfg.get("extra_args"))
    _run(ctx, cmd, "texture.log")
